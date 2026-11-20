"""Test fixtures.

Tests never call a real LLM. A tiny OpenAI-compatible server runs in a background thread and
LiteLLM talks to it over real HTTP (``LLM_MODEL=openai/fake-model``, ``LLM_API_BASE``). Its
behaviour is chosen by the API key, so real 429 / 5xx / 401 responses exercise LiteLLM's
genuine exception mapping and the Router's fallback, not just the simulation hook.

Key conventions understood by the fake provider:
  good-*      -> 200 with a reply that echoes the conversation (proves history is sent)
  fail429-*   -> HTTP 429 (rate limit / quota)
  fail500-*   -> HTTP 500,  fail502-* -> 502,  fail503-* -> 503
  fail401-*   -> HTTP 401 (revoked key)
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

LIVE_DELAY_SECONDS = 0.06  # makes "cache hit is faster than a live call" observable


def _words(text: str) -> int:
    return len(text.split())


@dataclass
class FakeProvider:
    base_url: str
    calls: list[dict] = field(default_factory=list)

    def reset(self) -> None:
        self.calls.clear()

    def calls_for(self, key_prefix: str) -> int:
        return sum(1 for c in self.calls if c["api_key"].startswith(key_prefix))


def _build_fake_app(provider: FakeProvider) -> FastAPI:
    fake = FastAPI()

    @fake.post("/v1/chat/completions")
    async def completions(request: Request):
        api_key = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        body = await request.json()
        provider.calls.append({"api_key": api_key, "messages": body.get("messages", [])})
        for status in (429, 500, 502, 503, 401):
            if api_key.startswith(f"fail{status}"):
                return JSONResponse({"error": {"message": f"fake provider error {status}", "type": "fake"}}, status_code=status)
        await asyncio.sleep(LIVE_DELAY_SECONDS)
        messages = body["messages"]
        user_turns = [m["content"] for m in messages if m["role"] == "user"]
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        reply = (
            f"You said: {user_turns[-1]} | first message: {user_turns[0]} | "
            f"user turns: {len(user_turns)} | system: {system[:40]}"
        )
        prompt_tokens = sum(_words(m["content"]) for m in messages)
        completion_tokens = _words(reply)
        return {
            "id": f"chatcmpl-{len(provider.calls)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "fake-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    return fake


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def fake_provider() -> Iterator[FakeProvider]:
    port = _free_port()
    provider = FakeProvider(base_url=f"http://127.0.0.1:{port}/v1")
    server = uvicorn.Server(uvicorn.Config(_build_fake_app(provider), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "fake provider did not start"
    yield provider
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def make_client(tmp_path, fake_provider) -> Iterator[Callable[..., TestClient]]:
    """Build a TestClient for an app configured through environment-style settings."""
    opened: list[TestClient] = []

    def factory(*, keys: str = "good-1,good-2", db_name: str = "app.db", **env: str) -> TestClient:
        fake_provider.reset()
        values = {
            "LLM_API_KEYS": keys,
            "LLM_MODEL": "openai/fake-model",
            "LLM_API_BASE": fake_provider.base_url,
            "DATABASE_URL": f"sqlite:///{tmp_path / db_name}",
            "ENABLE_FAILURE_SIMULATION": "true",
            "CACHE_TYPE": "local",
            "CACHE_DIR": str(tmp_path / "cache"),
            "ALLOWED_ORIGINS": "http://localhost:3000",
            "LOG_LEVEL": "WARNING",
        }
        values.update(env)
        client = TestClient(create_app(Settings.from_env(values)))
        client.__enter__()
        opened.append(client)
        return client

    yield factory
    for client in opened:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def new_session(prefix: str = "s") -> str:
    return f"{prefix}-{time.time_ns()}"
