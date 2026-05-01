from __future__ import annotations

import sqlite3
import struct
import time
import uuid
from pathlib import Path
from typing import Any

import sqlite_vec


def _serialize(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


class VectorStore:
    """sqlite-vec backed vector store for RAG. Decoupled from chat Store for clarity."""

    def __init__(self, db_path: Path | str, dim: int = 768):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.dim = dim
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                title TEXT,
                kind TEXT,
                created_at REAL NOT NULL,
                metadata TEXT
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                seq INTEGER NOT NULL,
                content TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vec USING vec0(
                embedding float[{self.dim}]
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def add_document(self, *, source: str, title: str | None, kind: str) -> str:
        did = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO documents (id, source, title, kind, created_at) VALUES (?, ?, ?, ?, ?)",
            (did, source, title, kind, time.time()),
        )
        self._conn.commit()
        return did

    def add_chunks(self, document_id: str, chunks: list[str], embeddings: list[list[float]]) -> None:
        assert len(chunks) == len(embeddings)
        cur = self._conn.cursor()
        for seq, (c, e) in enumerate(zip(chunks, embeddings)):
            cur.execute(
                "INSERT INTO chunks (document_id, seq, content) VALUES (?, ?, ?)",
                (document_id, seq, c),
            )
            chunk_id = cur.lastrowid
            cur.execute(
                "INSERT INTO chunk_vec (rowid, embedding) VALUES (?, ?)",
                (chunk_id, _serialize(e)),
            )
        self._conn.commit()

    def search(self, embedding: list[float], k: int = 5) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT c.id, c.content, c.document_id, d.source, d.title, v.distance
            FROM chunk_vec v
            JOIN chunks c ON c.id = v.rowid
            JOIN documents d ON d.id = c.document_id
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (_serialize(embedding), k),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_documents(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, source, title, kind, created_at FROM documents ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_document(self, did: str) -> None:
        chunk_ids = [
            r["id"] for r in self._conn.execute("SELECT id FROM chunks WHERE document_id = ?", (did,))
        ]
        for cid in chunk_ids:
            self._conn.execute("DELETE FROM chunk_vec WHERE rowid = ?", (cid,))
        self._conn.execute("DELETE FROM chunks WHERE document_id = ?", (did,))
        self._conn.execute("DELETE FROM documents WHERE id = ?", (did,))
        self._conn.commit()
