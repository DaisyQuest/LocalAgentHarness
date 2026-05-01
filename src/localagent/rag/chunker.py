from __future__ import annotations

import re


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    """Sentence-aware sliding chunker. Falls back to char windows on long blocks."""
    text = text.strip()
    if not text:
        return []
    sents = re.split(r"(?<=[.!?])\s+|\n\n+", text)
    chunks: list[str] = []
    cur = ""
    for s in sents:
        if not s:
            continue
        if len(cur) + len(s) + 1 <= chunk_size:
            cur = f"{cur} {s}".strip()
        else:
            if cur:
                chunks.append(cur)
            if len(s) > chunk_size:
                for i in range(0, len(s), chunk_size - overlap):
                    chunks.append(s[i : i + chunk_size])
                cur = ""
            else:
                cur = s
    if cur:
        chunks.append(cur)

    if overlap > 0 and len(chunks) > 1:
        out = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-overlap:]
            out.append(prev_tail + " " + chunks[i])
        return out
    return chunks
