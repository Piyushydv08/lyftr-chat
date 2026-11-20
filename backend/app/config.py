"""Application settings, read once from environment variables.

Every runtime knob lives here so the rest of the code never touches ``os.environ``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MODEL = "gemini/gemini-2.5-flash-lite"
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful, concise assistant. Answer clearly and accurately. "
    "If you do not know something, say so."
)

_TRUE = {"1", "true", "yes", "on"}


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in _TRUE


def _csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def _float(value: str | None, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def sqlite_path_from_url(url: str) -> str:
    """Turn ``sqlite:////data/app.db`` (absolute) or ``sqlite:///./app.db`` (relative) into a path."""
    url = url.strip()
    prefix = "sqlite:///"
    if url.startswith(prefix):
        return url[len(prefix):] or "app.db"
    if url.startswith("sqlite://"):
        raise ValueError("DATABASE_URL must look like sqlite:////absolute/path.db or sqlite:///relative.db")
    # Allow a bare file path as a convenience.
    return url


@dataclass(frozen=True)
class Settings:
    llm_api_keys: tuple[str, ...]
    llm_model: str
    llm_models: tuple[str, ...]
    llm_api_base: str | None
    llm_timeout_seconds: float
    llm_max_output_tokens: int
    database_url: str
    allowed_origins: tuple[str, ...]
    allowed_origin_regex: str | None
    enable_failure_simulation: bool
    default_system_prompt: str
    cache_type: str
    cache_dir: str
    cache_ttl_seconds: int
    history_max_messages: int
    log_level: str
    enable_docs: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        cache_type = (env.get("CACHE_TYPE") or "local").strip().lower()
        if cache_type not in {"local", "disk"}:
            cache_type = "local"
        return cls(
            llm_api_keys=_csv(env.get("LLM_API_KEYS")),
            llm_model=(env.get("LLM_MODEL") or DEFAULT_MODEL).strip(),
            llm_models=tuple(m.strip() for m in (env.get("LLM_MODELS") or "").split(",")) if env.get("LLM_MODELS") else (),
            llm_api_base=(env.get("LLM_API_BASE") or "").strip() or None,
            llm_timeout_seconds=_float(env.get("LLM_TIMEOUT_SECONDS"), 30.0),
            llm_max_output_tokens=_int(env.get("LLM_MAX_OUTPUT_TOKENS"), 1024),
            database_url=(env.get("DATABASE_URL") or "sqlite:///./data/app.db").strip(),
            allowed_origins=_csv(env.get("ALLOWED_ORIGINS")) or ("http://localhost:3000", "http://127.0.0.1:3000"),
            allowed_origin_regex=(env.get("ALLOWED_ORIGIN_REGEX") or "").strip() or None,
            enable_failure_simulation=_bool(env.get("ENABLE_FAILURE_SIMULATION"), False),
            default_system_prompt=(env.get("DEFAULT_SYSTEM_PROMPT") or DEFAULT_SYSTEM_PROMPT).strip(),
            cache_type=cache_type,
            cache_dir=(env.get("CACHE_DIR") or "./data/llm_cache").strip(),
            cache_ttl_seconds=_int(env.get("CACHE_TTL_SECONDS"), 86400),
            history_max_messages=_int(env.get("HISTORY_MAX_MESSAGES"), 50),
            log_level=(env.get("LOG_LEVEL") or "INFO").strip().upper(),
            enable_docs=_bool(env.get("ENABLE_DOCS"), True),
        )

    @property
    def database_path(self) -> str:
        return sqlite_path_from_url(self.database_url)

    def model_for_key(self, index: int) -> str:
        """Model for the key at ``index`` (0-based); ``LLM_MODELS`` overrides ``LLM_MODEL`` per key."""
        if index < len(self.llm_models) and self.llm_models[index]:
            return self.llm_models[index]
        return self.llm_model
