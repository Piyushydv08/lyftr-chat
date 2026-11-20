"""SQLite persistence for chat sessions.

Uses the standard-library ``sqlite3`` driver with one short-lived connection per
operation (cheap for SQLite, and safe across FastAPI's worker threads). Calls are
blocking, so the async layer runs them through ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,   -- 'user' or 'assistant'
    content     TEXT NOT NULL,
    metadata    TEXT,            -- JSON string, assistant messages only
    created_at  TEXT NOT NULL    -- server time, ISO-8601
);

CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages (session_id, id);

-- One row per session. Holds the per-session system prompt override
-- (NULL means "use the server default"). Written in the same transaction
-- as the first successful exchange, so a session exists only once it has history.
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    system_prompt  TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
"""

_UNSET = object()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class SessionRow:
    session_id: str
    system_prompt: str | None
    created_at: str
    updated_at: str


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    # -- connection helpers -------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def init_schema(self) -> None:
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            conn.commit()

    def ping(self) -> bool:
        try:
            with closing(self._connect()) as conn:
                conn.execute("SELECT 1 FROM messages LIMIT 1").fetchall()
            return True
        except sqlite3.Error:
            return False

    # -- reads ----------------------------------------------------------------
    def get_session(self, session_id: str) -> SessionRow | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT session_id, system_prompt, created_at, updated_at FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is not None:
                return SessionRow(**dict(row))
            # Tolerate rows written without a sessions entry (e.g. imported data).
            first = conn.execute(
                "SELECT created_at FROM messages WHERE session_id = ? ORDER BY id LIMIT 1",
                (session_id,),
            ).fetchone()
            if first is None:
                return None
            return SessionRow(session_id, None, first["created_at"], first["created_at"])

    def get_history_for_llm(self, session_id: str, max_messages: int) -> list[dict[str, str]]:
        """Most recent ``max_messages`` turns, oldest first, as LLM chat messages (0 = unlimited)."""
        with closing(self._connect()) as conn:
            if max_messages > 0:
                rows = conn.execute(
                    "SELECT role, content FROM ("
                    "  SELECT id, role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?"
                    ") ORDER BY id ASC",
                    (session_id, max_messages),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC",
                    (session_id,),
                ).fetchall()
        history = [{"role": r["role"], "content": r["content"]} for r in rows]
        # If truncation cut a pair in half, never start the window on an assistant turn.
        while history and history[0]["role"] != "user":
            history.pop(0)
        return history

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT role, content, metadata, created_at FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            item: dict[str, Any] = {"role": r["role"], "content": r["content"]}
            if r["metadata"]:
                try:
                    item["metadata"] = json.loads(r["metadata"])
                except json.JSONDecodeError:
                    item["metadata"] = None
            item["created_at"] = r["created_at"]
            out.append(item)
        return out

    # -- writes ---------------------------------------------------------------
    def append_exchange(
        self,
        session_id: str,
        user_content: str,
        assistant_content: str,
        assistant_metadata: dict[str, Any],
        system_prompt: Any = _UNSET,
    ) -> None:
        """Persist one user/assistant pair atomically (and create/update the session row).

        ``system_prompt`` semantics: leave unset to keep the stored value, ``None`` to reset
        to the server default, or a string to store a per-session override.
        """
        user_ts = utc_now_iso()
        with closing(self._connect()) as conn:
            with conn:  # one transaction: both messages or neither
                existing = conn.execute(
                    "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
                ).fetchone()
                if existing is None:
                    conn.execute(
                        "INSERT INTO sessions (session_id, system_prompt, created_at, updated_at) VALUES (?, ?, ?, ?)",
                        (session_id, None if system_prompt is _UNSET else system_prompt, user_ts, user_ts),
                    )
                elif system_prompt is not _UNSET:
                    conn.execute(
                        "UPDATE sessions SET system_prompt = ?, updated_at = ? WHERE session_id = ?",
                        (system_prompt, user_ts, session_id),
                    )
                else:
                    conn.execute(
                        "UPDATE sessions SET updated_at = ? WHERE session_id = ?", (user_ts, session_id)
                    )
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, 'user', ?, NULL, ?)",
                    (session_id, user_content, user_ts),
                )
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, 'assistant', ?, ?, ?)",
                    (session_id, assistant_content, json.dumps(assistant_metadata), utc_now_iso()),
                )


UNSET = _UNSET
