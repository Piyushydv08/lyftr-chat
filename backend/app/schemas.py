"""Request/response models for the public API contract."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

SessionId = Annotated[str, StringConstraints(min_length=1, max_length=128)]
# Trimmed first, then length-checked: "1-4000 chars after trimming whitespace".
UserMessage = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: SessionId
    message: UserMessage
    system_prompt: str | None = Field(default=None, max_length=8000)

    @field_validator("session_id")
    @classmethod
    def session_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("session_id must not be empty or whitespace")
        return value


class ChatMetadata(BaseModel):
    resolution_source: Literal["LIVE_INFERENCE", "LLM_CACHE"]
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: ChatMetadata


class HealthResponse(BaseModel):
    status: Literal["ok"]
    keys_configured: int
    cache_enabled: bool
    failure_simulation_enabled: bool
