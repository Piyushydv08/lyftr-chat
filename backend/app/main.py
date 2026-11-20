"""FastAPI application: the three contract endpoints plus error handling and CORS."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.chat_service import ChatService
from app.config import Settings
from app.db import Database
from app.llm.gateway import AllProvidersUnavailable, LLMGateway
from app.llm.simulation import SimulationHeaderError, parse_simulation_header
from app.schemas import ChatRequest, ChatResponse, HealthResponse

logger = logging.getLogger("app")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("app").setLevel(getattr(logging, level, logging.INFO))


def create_app(
    settings: Settings | None = None,
    *,
    completion_overrides: dict[str, Any] | None = None,
) -> FastAPI:
    """Build the app. ``completion_overrides`` lets tests inject LiteLLM mock responses."""
    settings = settings or Settings.from_env()
    _configure_logging(settings.log_level)

    db = Database(settings.database_path)
    gateway = LLMGateway(settings, completion_overrides=completion_overrides)
    service = ChatService(settings, db, gateway)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await asyncio.to_thread(db.init_schema)  # schema is created automatically on startup
        logger.info(
            "started: keys=%s models=%s cache=%s failure_simulation=%s db=%s",
            gateway.key_ids,
            sorted({s.model for s in gateway.slots}),
            settings.cache_type,
            settings.enable_failure_simulation,
            settings.database_path,
        )
        if gateway.keys_configured < 2:
            logger.warning("LLM_API_KEYS has %s key(s); failover needs at least 2", gateway.keys_configured)
        yield

    app = FastAPI(
        title="Lyftr AI Chat API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.gateway = gateway

    # -- errors -----------------------------------------------------------------
    @app.exception_handler(AllProvidersUnavailable)
    async def _all_providers_unavailable(_: Request, __: AllProvidersUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "all providers unavailable"})

    @app.exception_handler(SimulationHeaderError)
    async def _bad_simulation_header(_: Request, exc: SimulationHeaderError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.middleware("http")
    async def _never_leak_stack_traces(request: Request, call_next):
        # Registered before CORS so even unexpected 500s carry CORS headers and stay JSON.
        try:
            return await call_next(request)
        except Exception:
            logger.exception("unhandled error on %s %s", request.method, request.url.path)
            return JSONResponse(status_code=500, content={"detail": "internal server error"})

    origins = list(settings.allowed_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if "*" in origins else origins,
        allow_origin_regex=settings.allowed_origin_regex,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Simulate-Failure"],
        max_age=600,
    )

    # -- endpoints ----------------------------------------------------------------
    @app.get("/health", response_model=HealthResponse)
    async def health():
        db_ok = await asyncio.to_thread(db.ping)
        if not db_ok:
            return JSONResponse(status_code=503, content={"detail": "database unreachable", "status": "error"})
        if gateway.keys_configured == 0:
            return JSONResponse(status_code=503, content={"detail": "no API keys configured", "status": "error"})
        return HealthResponse(
            status="ok",
            keys_configured=gateway.keys_configured,
            cache_enabled=gateway.cache_enabled,
            failure_simulation_enabled=settings.enable_failure_simulation,
        )

    @app.post("/chat", response_model=ChatResponse)
    async def chat(
        body: ChatRequest,
        x_simulate_failure: str | None = Header(default=None, alias="X-Simulate-Failure"),
    ) -> ChatResponse:
        started_at = time.perf_counter()
        simulated: dict[str, int] | None = None
        if settings.enable_failure_simulation and x_simulate_failure is not None:
            simulated = parse_simulation_header(x_simulate_failure, gateway.key_ids)
        response = await service.chat(
            session_id=body.session_id,
            message=body.message,
            system_prompt=body.system_prompt,
            simulated_failures=simulated,
            started_at=started_at,
        )
        meta = response.metadata
        logger.info(
            "chat session=%s source=%s key=%s fallbacks=%s latency_ms=%s tokens=%s/%s%s",
            body.session_id,
            meta.resolution_source,
            meta.key_id,
            meta.fallback_attempts,
            meta.latency_ms,
            meta.input_tokens,
            meta.output_tokens,
            f" simulated={simulated}" if simulated else "",
        )
        return response

    @app.get("/sessions/{session_id}/messages")
    async def session_messages(session_id: str):
        session = await asyncio.to_thread(db.get_session, session_id)
        if session is None:
            return JSONResponse(status_code=404, content={"detail": "session not found"})
        messages = await asyncio.to_thread(db.get_messages, session_id)
        return {"session_id": session_id, "messages": messages}

    return app


app = create_app()
