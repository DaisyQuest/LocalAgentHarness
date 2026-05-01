from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from ..core import Message

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_calls TEXT,
    tool_call_id TEXT,
    name TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, seq);

CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    model TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    latency_ms REAL,
    created_at REAL NOT NULL
);
"""


class Store:
    """SQLite-backed conversation + telemetry store. sqlite-vec hooks added when RAG ships."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- conversations ---
    def create_conversation(self, title: str | None = None) -> str:
        cid = str(uuid.uuid4())
        now = time.time()
        self._conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (cid, title, now, now),
        )
        self._conn.commit()
        return cid

    def set_title(self, cid: str, title: str) -> None:
        self._conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, time.time(), cid),
        )
        self._conn.commit()

    def get_title(self, cid: str) -> str | None:
        row = self._conn.execute("SELECT title FROM conversations WHERE id = ?", (cid,)).fetchone()
        return row["title"] if row else None

    def count_user_messages(self, cid: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE conversation_id = ? AND role = 'user'",
            (cid,),
        ).fetchone()
        return int(row["c"]) if row else 0

    def list_conversations(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_conversation(self, cid: str) -> None:
        self._conn.execute("DELETE FROM messages WHERE conversation_id = ?", (cid,))
        self._conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))
        self._conn.commit()

    # --- messages ---
    def append_message(self, conversation_id: str, msg: Message) -> str:
        mid = str(uuid.uuid4())
        seq_row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 AS s FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        seq = seq_row["s"]
        self._conn.execute(
            "INSERT INTO messages "
            "(id, conversation_id, seq, role, content, tool_calls, tool_call_id, name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                mid,
                conversation_id,
                seq,
                msg.role,
                msg.content,
                json.dumps([tc.model_dump() for tc in msg.tool_calls]) if msg.tool_calls else None,
                msg.tool_call_id,
                msg.name,
                time.time(),
            ),
        )
        self._conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (time.time(), conversation_id),
        )
        self._conn.commit()
        return mid

    def get_messages(self, conversation_id: str) -> list[Message]:
        rows = self._conn.execute(
            "SELECT role, content, tool_calls, tool_call_id, name FROM messages "
            "WHERE conversation_id = ? ORDER BY seq ASC",
            (conversation_id,),
        ).fetchall()
        out: list[Message] = []
        for r in rows:
            tc = json.loads(r["tool_calls"]) if r["tool_calls"] else []
            out.append(
                Message(
                    role=r["role"],
                    content=r["content"],
                    tool_calls=tc,
                    tool_call_id=r["tool_call_id"],
                    name=r["name"],
                )
            )
        return out

    # --- telemetry ---
    def log_telemetry(
        self,
        *,
        model: str,
        conversation_id: str | None,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
    ) -> None:
        self._conn.execute(
            "INSERT INTO telemetry "
            "(conversation_id, model, prompt_tokens, completion_tokens, latency_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, model, prompt_tokens, completion_tokens, latency_ms, time.time()),
        )
        self._conn.commit()
