"""LiteLLM gateway: key pool, ordered failover and response caching.

Design (see README "Design decisions"):

* Every API key is its own Router *model group* (``key_1``, ``key_2``, ...) with exactly one
  deployment. ``fallbacks=[{"key_1": ["key_2", ...]}]`` gives a deterministic failover order,
  so ``key_1=429`` always lands on ``key_2`` and ``fallback_attempts`` is meaningful.
* ``num_retries=0`` and per-deployment ``max_retries=0``: a failing key is abandoned
  immediately instead of being retried while the user waits.
* ``disable_cooldowns=True``: simulated failures must affect one request only, and with one
  deployment per group a cooldown would only remove that key from its own group.
* LiteLLM's built-in cache (in-memory, or disk which is SQLite-backed) with
  ``caching_groups`` so all keys that serve the same model share one cache namespace. The
  cache key is the model plus the full message list (system + history + new message).
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from typing import Any, Literal

import litellm
from litellm import Router
from litellm.caching.caching import Cache

from app.config import Settings
from app.llm.simulation import KeyPoolHook, RequestState, current_request

logger = logging.getLogger("app.llm")

ResolutionSource = Literal["LIVE_INFERENCE", "LLM_CACHE"]

# When a pre-call hook rejects a deployment, the Router has already created (but never awaits)
# the provider coroutine. That is expected for simulated failures, so silence the warning.
warnings.filterwarnings("ignore", message=r"coroutine .* was never awaited", category=RuntimeWarning)


class AllProvidersUnavailable(Exception):
    """Every key in the pool failed (429 / quota / 5xx / timeout / auth) for this request."""


@dataclass(frozen=True)
class KeySlot:
    key_id: str
    model: str
    api_key: str

    def __repr__(self) -> str:  # never leak the key through logs or tracebacks
        return f"KeySlot(key_id={self.key_id!r}, model={self.model!r}, api_key='***')"


@dataclass(frozen=True)
class CompletionResult:
    reply: str
    resolution_source: ResolutionSource
    model: str
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int


def _configure_litellm_globals(log_level: str) -> None:
    litellm.suppress_debug_info = True
    litellm.drop_params = True  # ignore params a provider does not support instead of failing
    if hasattr(litellm, "telemetry"):
        litellm.telemetry = False
    # LiteLLM logs every failed attempt with a full traceback. We log one redacted line per
    # request ourselves, so keep LiteLLM quiet unless explicitly debugging.
    litellm_level = logging.DEBUG if log_level == "DEBUG" else logging.CRITICAL
    for name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "litellm"):
        logging.getLogger(name).setLevel(litellm_level)


def _usage_value(usage: Any, name: str) -> int | None:
    if usage is None:
        return None
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class LLMGateway:
    def __init__(self, settings: Settings, *, completion_overrides: dict[str, Any] | None = None) -> None:
        self.settings = settings
        self._completion_overrides = dict(completion_overrides or {})
        self.slots: list[KeySlot] = [
            KeySlot(key_id=f"key_{i + 1}", model=settings.model_for_key(i), api_key=key)
            for i, key in enumerate(settings.llm_api_keys)
        ]
        self._models_by_key = {s.key_id: s.model for s in self.slots}
        self._secrets = [s.api_key for s in self.slots if s.api_key]

        _configure_litellm_globals(settings.log_level)
        self._configure_cache()
        self._hook = KeyPoolHook(self._models_by_key)
        # Replace any hook left by a previous gateway (tests build several apps per process).
        litellm.callbacks = [cb for cb in litellm.callbacks if not isinstance(cb, KeyPoolHook)] + [self._hook]
        self.router: Router | None = self._build_router() if self.slots else None

    # -- setup ------------------------------------------------------------------
    def _configure_cache(self) -> None:
        ttl = self.settings.cache_ttl_seconds if self.settings.cache_ttl_seconds > 0 else None
        common = {"ttl": ttl, "supported_call_types": ["acompletion", "completion"]}
        if self.settings.cache_type == "disk":
            litellm.cache = Cache(type="disk", disk_cache_dir=self.settings.cache_dir, **common)
        else:
            litellm.cache = Cache(type="local", default_in_memory_ttl=ttl, **common)
        litellm.enable_cache = True  # no-op on newer versions, harmless on older ones

    def _build_router(self) -> Router:
        key_ids = [s.key_id for s in self.slots]
        model_list = []
        for slot in self.slots:
            params: dict[str, Any] = {
                "model": slot.model,
                "api_key": slot.api_key,
                "timeout": self.settings.llm_timeout_seconds,
                "max_retries": 0,
            }
            if self.settings.llm_api_base:
                params["api_base"] = self.settings.llm_api_base
            model_list.append({"model_name": slot.key_id, "litellm_params": params, "model_info": {"id": slot.key_id}})

        # Keys serving the same model share cached answers; the model name is part of the
        # group so that changing LLM_MODEL never serves answers produced by another model.
        groups: dict[str, list[str]] = {}
        for slot in self.slots:
            groups.setdefault(slot.model, []).append(slot.key_id)
        caching_groups = [(model, *ids) for model, ids in groups.items()]

        return Router(
            model_list=model_list,
            fallbacks=[{key_ids[0]: key_ids[1:]}] if len(key_ids) > 1 else [],
            max_fallbacks=max(len(key_ids), 1),
            caching_groups=caching_groups,
            cache_responses=True,
            num_retries=0,
            disable_cooldowns=True,
            timeout=self.settings.llm_timeout_seconds,
        )

    # -- introspection ----------------------------------------------------------
    @property
    def key_ids(self) -> list[str]:
        return [s.key_id for s in self.slots]

    @property
    def keys_configured(self) -> int:
        return len(self.slots)

    @property
    def cache_enabled(self) -> bool:
        return litellm.cache is not None

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    # -- the call ---------------------------------------------------------------
    async def complete(
        self, messages: list[dict[str, str]], *, simulated_failures: dict[str, int] | None = None
    ) -> CompletionResult:
        if self.router is None:
            raise AllProvidersUnavailable("no API keys configured")

        state = RequestState(simulated=dict(simulated_failures or {}))
        token = current_request.set(state)
        kwargs: dict[str, Any] = dict(self._completion_overrides)
        if self.settings.llm_max_output_tokens > 0:
            kwargs.setdefault("max_tokens", self.settings.llm_max_output_tokens)
        if state.simulated:
            # The test hook must exercise the provider path: skip the cache *lookup* (the
            # successful answer is still stored) so failover is observable and *=429 is a 503.
            kwargs["cache"] = {"no-cache": True}
        try:
            response = await self.router.acompletion(model=self.slots[0].key_id, messages=messages, **kwargs)
        except Exception as exc:  # every key failed, or the router itself errored
            status = getattr(exc, "status_code", None)
            logger.warning(
                "all providers unavailable: attempts=%s last_error=%s status=%s detail=%s",
                state.attempts,
                type(exc).__name__,
                status,
                self.redact(str(exc))[:300],
            )
            raise AllProvidersUnavailable("all providers unavailable") from None
        finally:
            current_request.reset(token)

        return self._to_result(response, messages, state)

    def _to_result(self, response: Any, messages: list[dict[str, str]], state: RequestState) -> CompletionResult:
        hidden: dict[str, Any] = getattr(response, "_hidden_params", None) or {}
        cache_hit = bool(hidden.get("cache_hit"))
        answered_by = hidden.get("model_id") or (state.attempts[-1] if state.attempts else self.slots[0].key_id)
        model = self._models_by_key.get(answered_by, self.slots[0].model)

        try:
            reply = response.choices[0].message.content or ""
        except (AttributeError, IndexError):
            reply = ""
        if not reply.strip():
            logger.warning("empty completion from %s (cache_hit=%s)", answered_by, cache_hit)
            raise AllProvidersUnavailable("all providers unavailable")

        usage = getattr(response, "usage", None)
        input_tokens = _usage_value(usage, "prompt_tokens")
        output_tokens = _usage_value(usage, "completion_tokens")
        if input_tokens is None:  # provider omitted usage: count locally with the model's tokenizer
            input_tokens = litellm.token_counter(model=model, messages=messages)
        if output_tokens is None:
            output_tokens = litellm.token_counter(model=model, text=reply, count_response_tokens=True)

        if cache_hit:
            key_id: str | None = None
            fallback_attempts = 0
        else:
            key_id = answered_by
            # Keys tried (and failed) before the one that answered, in Router order.
            fallback_attempts = state.attempts.index(answered_by) if answered_by in state.attempts else max(len(state.attempts) - 1, 0)
            if fallback_attempts:
                failed = state.attempts[:fallback_attempts]
                logger.info("failover: %s failed, answered by %s", failed, key_id)

        return CompletionResult(
            reply=reply,
            resolution_source="LLM_CACHE" if cache_hit else "LIVE_INFERENCE",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            key_id=key_id,
            fallback_attempts=fallback_attempts,
        )
