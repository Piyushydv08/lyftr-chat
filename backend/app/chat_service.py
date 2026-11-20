"""Chat orchestration: load history -> call the gateway -> persist the exchange."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.config import Settings
from app.db import UNSET, Database
from app.llm.gateway import LLMGateway
from app.schemas import ChatMetadata, ChatResponse


class ChatService:
    def __init__(self, settings: Settings, db: Database, gateway: LLMGateway) -> None:
        self.settings = settings
        self.db = db
        self.gateway = gateway

    async def chat(
        self,
        *,
        session_id: str,
        message: str,
        system_prompt: str | None,
        simulated_failures: dict[str, int] | None,
        started_at: float,
    ) -> ChatResponse:
        session = await asyncio.to_thread(self.db.get_session, session_id)

        # system_prompt: omitted -> keep the session's stored prompt; blank -> reset to the
        # server default; text -> override for this session from now on.
        store_prompt: Any = UNSET
        if system_prompt is None:
            effective_prompt = (session.system_prompt if session and session.system_prompt else None)
        elif system_prompt.strip():
            effective_prompt = system_prompt.strip()
            store_prompt = effective_prompt
        else:
            effective_prompt = None
            store_prompt = None
        effective_prompt = effective_prompt or self.settings.default_system_prompt

        history = await asyncio.to_thread(
            self.db.get_history_for_llm, session_id, self.settings.history_max_messages
        )
        llm_messages = [{"role": "system", "content": effective_prompt}, *history, {"role": "user", "content": message}]

        # Raises AllProvidersUnavailable -> 503; nothing below runs, so nothing is stored.
        result = await self.gateway.complete(llm_messages, simulated_failures=simulated_failures)

        metadata = ChatMetadata(
            resolution_source=result.resolution_source,
            model=result.model,
            latency_ms=max(int(round((time.perf_counter() - started_at) * 1000)), 0),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            key_id=result.key_id,
            fallback_attempts=result.fallback_attempts,
        )
        await asyncio.to_thread(
            self.db.append_exchange,
            session_id,
            message,
            result.reply,
            metadata.model_dump(),
            store_prompt,
        )
        return ChatResponse(session_id=session_id, reply=result.reply, metadata=metadata)
