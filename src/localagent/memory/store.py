from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..config import MemoryConfig
from ..core import Provider
from .auto_save import MemoryCandidate, _cosine
from .backends import MemoryBackend, PgVectorBackend, SqliteVecBackend


class Memory(BaseModel):
    id: str
    text: str
    kind: str
    metadata: dict[str, Any] = {}


class MemoryStore:
    """Long-term semantic memory. Pluggable backend (sqlite-vec default, pgvector optional)."""

    def __init__(
        self,
        provider: Provider,
        embed_model: str,
        config: MemoryConfig,
        sqlite_path: Path,
        dim: int = 768,
    ):
        self.provider = provider
        self.embed_model = embed_model
        self.config = config
        self.dim = dim
        self.backend: MemoryBackend
        if config.backend == "pgvector":
            if not config.pg_dsn:
                raise RuntimeError("memory.pg_dsn required when backend=pgvector")
            self.backend = PgVectorBackend(config.pg_dsn, dim=dim)
        else:
            self.backend = SqliteVecBackend(sqlite_path, dim=dim)

    def close(self) -> None:
        self.backend.close()

    async def remember(self, text: str, *, kind: str = "fact", metadata: dict[str, Any] | None = None) -> str:
        emb = (await self.provider.embed(self.embed_model, [text]))[0]
        return self.backend.add(text=text, embedding=emb, kind=kind, metadata=metadata or {})

    async def remember_candidates(
        self,
        candidates: list[MemoryCandidate],
        *,
        min_importance: int,
        dedup_threshold: float,
    ) -> list[dict[str, Any]]:
        """Embed each candidate, skip duplicates against existing memories, save the rest.

        Returns a list of {action, text, kind, importance} for telemetry.
        """
        results: list[dict[str, Any]] = []
        kept = [c for c in candidates if c.importance >= min_importance]
        if not kept:
            return [{"action": "skip", "reason": "below_importance", "text": c.text, "importance": c.importance} for c in candidates]
        embs = await self.provider.embed(self.embed_model, [c.text for c in kept])
        for cand, emb in zip(kept, embs):
            hits = self.backend.search(emb, k=3)
            dup_score = max((1.0 - h.get("distance", 1.0) for h in hits), default=0.0)
            # backends report sqlite-vec L2-distance or pgvector cosine-distance; both decrease with similarity.
            # 1 - distance is a workable proxy when embeddings are normalized.
            if dup_score >= dedup_threshold:
                results.append({"action": "dedup", "score": dup_score, "text": cand.text, "kind": cand.kind, "importance": cand.importance})
                continue
            mid = self.backend.add(
                text=cand.text, embedding=emb, kind=cand.kind,
                metadata={"importance": cand.importance, "rationale": cand.rationale, "auto": True},
            )
            results.append({"action": "save", "id": mid, "text": cand.text, "kind": cand.kind, "importance": cand.importance})
        return results

    async def recall(self, query: str, k: int | None = None) -> list[dict[str, Any]]:
        emb = (await self.provider.embed(self.embed_model, [query]))[0]
        return self.backend.search(emb, k=k or self.config.recall_k)

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.backend.list(limit=limit)

    def forget(self, mid: str) -> None:
        self.backend.delete(mid)

    @staticmethod
    def format_for_prompt(hits: list[dict[str, Any]]) -> str:
        if not hits:
            return ""
        lines = ["<long_term_memory>"]
        for h in hits:
            lines.append(f"- [{h['kind']}] {h['text']}")
        lines.append("</long_term_memory>")
        return "\n".join(lines)
