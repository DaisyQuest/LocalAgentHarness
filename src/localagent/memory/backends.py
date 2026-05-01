from __future__ import annotations

import sqlite3
import struct
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import sqlite_vec


def _ser(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


class MemoryBackend(ABC):
    """Pluggable vector backend for long-term memory."""

    @abstractmethod
    def add(self, *, text: str, embedding: list[float], kind: str, metadata: dict[str, Any]) -> str: ...

    @abstractmethod
    def search(self, embedding: list[float], k: int) -> list[dict[str, Any]]: ...

    @abstractmethod
    def list(self, limit: int = 200) -> list[dict[str, Any]]: ...

    @abstractmethod
    def delete(self, mid: str) -> None: ...

    def close(self) -> None: ...


class SqliteVecBackend(MemoryBackend):
    def __init__(self, db_path: Path | str, dim: int = 768):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.dim = dim
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                kind TEXT NOT NULL,
                metadata TEXT,
                created_at REAL NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(
                id TEXT PRIMARY KEY,
                embedding float[{dim}]
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def add(self, *, text: str, embedding: list[float], kind: str, metadata: dict[str, Any]) -> str:
        import json
        mid = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO memories (id, text, kind, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
            (mid, text, kind, json.dumps(metadata), time.time()),
        )
        self._conn.execute(
            "INSERT INTO memory_vec (id, embedding) VALUES (?, ?)", (mid, _ser(embedding))
        )
        self._conn.commit()
        return mid

    def search(self, embedding: list[float], k: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT m.id, m.text, m.kind, m.metadata, m.created_at, v.distance
            FROM memory_vec v
            JOIN memories m ON m.id = v.id
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (_ser(embedding), k),
        ).fetchall()
        return [dict(r) for r in rows]

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, text, kind, metadata, created_at FROM memories ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, mid: str) -> None:
        self._conn.execute("DELETE FROM memory_vec WHERE id = ?", (mid,))
        self._conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
        self._conn.commit()


class PgVectorBackend(MemoryBackend):
    """Postgres + pgvector. Requires `pip install psycopg[binary] pgvector` and a DSN."""

    def __init__(self, dsn: str, dim: int = 768):
        try:
            import psycopg  # noqa: F401
            from pgvector.psycopg import register_vector  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "pgvector backend requires: pip install 'psycopg[binary]' pgvector"
            ) from e
        import psycopg
        from pgvector.psycopg import register_vector

        self.dim = dim
        self._conn = psycopg.connect(dsn, autocommit=True)
        with self._conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(self._conn)
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS la_memories (
                    id UUID PRIMARY KEY,
                    text TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    metadata JSONB,
                    embedding vector({dim}),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS la_memories_emb_idx ON la_memories "
                "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
            )

    def close(self) -> None:
        self._conn.close()

    def add(self, *, text: str, embedding: list[float], kind: str, metadata: dict[str, Any]) -> str:
        import json
        import numpy as np

        mid = str(uuid.uuid4())
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO la_memories (id, text, kind, metadata, embedding) VALUES (%s, %s, %s, %s, %s)",
                (mid, text, kind, json.dumps(metadata), np.array(embedding, dtype=np.float32)),
            )
        return mid

    def search(self, embedding: list[float], k: int) -> list[dict[str, Any]]:
        import numpy as np
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT id::text, text, kind, metadata, created_at, embedding <=> %s AS distance "
                "FROM la_memories ORDER BY embedding <=> %s LIMIT %s",
                (np.array(embedding, dtype=np.float32), np.array(embedding, dtype=np.float32), k),
            )
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT id::text, text, kind, metadata, created_at FROM la_memories ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def delete(self, mid: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM la_memories WHERE id = %s", (mid,))
