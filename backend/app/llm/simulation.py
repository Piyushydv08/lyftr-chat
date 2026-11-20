"""Per-request key-failure simulation (the ``X-Simulate-Failure`` test hook).

The header is parsed into ``{key_id: status}``. During one request the map lives in a
``ContextVar``; a LiteLLM ``CustomLogger`` pre-call hook reads it every time the Router is
about to use a deployment (one deployment == one API key) and raises the same LiteLLM
exception the provider SDK would have raised for that HTTP status. The Router therefore
treats it exactly like a real provider failure and runs its normal fallback chain.

Because the map is request-scoped, a simulated failure can never leak into another request.
"""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass, field

import litellm
from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger("app.llm")

SIMULATABLE_STATUSES = frozenset({429, 500, 502, 503})
_KEY_ID_RE = re.compile(r"^key_[1-9][0-9]*$")


class SimulationHeaderError(ValueError):
    """Raised for a malformed ``X-Simulate-Failure`` header."""


def parse_simulation_header(raw: str, key_ids: list[str]) -> dict[str, int]:
    """Parse ``key_1=429,key_2=500`` or ``*=429`` into ``{"key_1": 429, ...}``.

    ``*`` expands to every configured key. Well-formed ids that are not configured
    (e.g. ``key_9`` with two keys) are ignored. Later entries override earlier ones.
    """
    result: dict[str, int] = {}
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise SimulationHeaderError("X-Simulate-Failure is empty; expected e.g. key_1=429 or *=429")
    for part in parts:
        if "=" not in part:
            raise SimulationHeaderError(f"invalid X-Simulate-Failure entry {part!r}; expected <key_id>=<status>")
        key, _, status_text = part.partition("=")
        key = key.strip().lower()
        status_text = status_text.strip()
        if not status_text.isdigit() or int(status_text) not in SIMULATABLE_STATUSES:
            raise SimulationHeaderError(
                f"invalid status {status_text!r} in X-Simulate-Failure; allowed: 429, 500, 502, 503"
            )
        status = int(status_text)
        if key == "*":
            for key_id in key_ids:
                result[key_id] = status
        elif _KEY_ID_RE.match(key):
            if key in key_ids:
                result[key] = status
        else:
            raise SimulationHeaderError(f"invalid key id {key!r} in X-Simulate-Failure; expected key_1, key_2, ... or *")
    return result


def provider_error(status: int, key_id: str, model: str) -> Exception:
    """The LiteLLM exception a provider call would raise for ``status``."""
    provider = model.split("/", 1)[0] if "/" in model else "openai"
    message = f"Simulated HTTP {status} for {key_id} (X-Simulate-Failure)"
    if status == 429:
        return litellm.RateLimitError(message=message, llm_provider=provider, model=model)
    if status == 500:
        return litellm.InternalServerError(message=message, llm_provider=provider, model=model)
    if status == 502:
        return litellm.BadGatewayError(message=message, llm_provider=provider, model=model)
    if status == 503:
        return litellm.ServiceUnavailableError(message=message, llm_provider=provider, model=model)
    return litellm.APIError(status_code=status, message=message, llm_provider=provider, model=model)


@dataclass
class RequestState:
    """What happened to one ``/chat`` request inside the Router."""

    simulated: dict[str, int] = field(default_factory=dict)
    attempts: list[str] = field(default_factory=list)  # key ids, in the order the Router tried them


current_request: ContextVar[RequestState | None] = ContextVar("current_llm_request", default=None)


class KeyPoolHook(CustomLogger):
    """Router pre-call hook: records each key attempt and injects simulated failures."""

    def __init__(self, models_by_key: dict[str, str]) -> None:
        super().__init__()
        self.models_by_key = models_by_key

    async def async_pre_call_check(self, deployment: dict, parent_otel_span=None):  # type: ignore[override]
        state = current_request.get()
        if state is None:
            return None
        key_id = (deployment.get("model_info") or {}).get("id")
        if key_id is None:
            return None
        state.attempts.append(key_id)
        status = state.simulated.get(key_id)
        if status is not None:
            logger.info("simulating HTTP %s on %s", status, key_id)
            raise provider_error(status, key_id, self.models_by_key.get(key_id, "unknown"))
        return None
