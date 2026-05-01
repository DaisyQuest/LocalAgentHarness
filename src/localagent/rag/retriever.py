from __future__ import annotations

from ..core import Provider
from .vectorstore import VectorStore


class Retriever:
    def __init__(self, provider: Provider, store: VectorStore, embed_model: str):
        self.provider = provider
        self.store = store
        self.embed_model = embed_model

    async def retrieve(self, query: str, k: int = 5) -> list[dict]:
        embeds = await self.provider.embed(self.embed_model, [query])
        if not embeds:
            return []
        return self.store.search(embeds[0], k=k)

    @staticmethod
    def format_context(hits: list[dict]) -> str:
        if not hits:
            return ""
        parts = ["<context>"]
        for h in hits:
            src = h.get("title") or h.get("source") or "?"
            parts.append(f"<doc source={src!r}>\n{h['content']}\n</doc>")
        parts.append("</context>")
        return "\n".join(parts)
