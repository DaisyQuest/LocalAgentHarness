from __future__ import annotations

from pathlib import Path

import httpx

from ..core import Provider
from .chunker import chunk_text
from .vectorstore import VectorStore

TEXT_EXTS = {".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".rst", ".json", ".yaml", ".yml", ".toml", ".html", ".css"}


async def _embed_chunks(provider: Provider, model: str, chunks: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    batch = 32
    for i in range(0, len(chunks), batch):
        out.extend(await provider.embed(model, chunks[i : i + batch]))
    return out


async def ingest_file(
    path: Path | str,
    *,
    provider: Provider,
    store: VectorStore,
    embed_model: str,
    chunk_size: int = 800,
    overlap: int = 100,
) -> str:
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="ignore")
    chunks = chunk_text(text, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        raise ValueError(f"no content in {p}")
    embeds = await _embed_chunks(provider, embed_model, chunks)
    did = store.add_document(source=str(p.resolve()), title=p.name, kind="file")
    store.add_chunks(did, chunks, embeds)
    return did


async def ingest_folder(
    folder: Path | str,
    *,
    provider: Provider,
    store: VectorStore,
    embed_model: str,
    chunk_size: int = 800,
    overlap: int = 100,
    exts: set[str] | None = None,
) -> list[str]:
    exts = exts or TEXT_EXTS
    folder = Path(folder)
    ids: list[str] = []
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            try:
                ids.append(
                    await ingest_file(
                        p,
                        provider=provider,
                        store=store,
                        embed_model=embed_model,
                        chunk_size=chunk_size,
                        overlap=overlap,
                    )
                )
            except Exception:
                continue
    return ids


async def ingest_url(
    url: str,
    *,
    provider: Provider,
    store: VectorStore,
    embed_model: str,
    chunk_size: int = 800,
    overlap: int = 100,
) -> str:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url, headers={"user-agent": "LocalAgent/0.2"})
        r.raise_for_status()
        text = r.text
    text = _html_to_text(text) if "<html" in text.lower()[:1000] else text
    chunks = chunk_text(text, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        raise ValueError(f"no content at {url}")
    embeds = await _embed_chunks(provider, embed_model, chunks)
    did = store.add_document(source=url, title=url, kind="url")
    store.add_chunks(did, chunks, embeds)
    return did


def _html_to_text(html: str) -> str:
    import re

    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"\s+", " ", html)
    return html.strip()
