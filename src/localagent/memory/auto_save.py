"""Significance-scored memory extraction.

The extractor receives a window of recent conversation, asks an LLM to surface
only durable, non-obvious facts worth remembering, and scores each on a 1–5
importance scale. The store then dedups against existing memories before saving.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from pydantic import BaseModel, Field

from ..core import ChatRequest, Message, Provider

EXTRACT_PROMPT = """You decide which durable facts from a conversation should become long-term memory.

Strong signals to KEEP (importance 4-5):
- Stable user identity, role, expertise, environment
- Project goals, constraints, deadlines, stakeholders
- Explicit preferences ("I always...", "never...", "prefer...")
- Decisions made and why
- Hard requirements or rejected approaches

Weak signals to DROP (importance 1-2):
- Today's task details, file paths the user is just looking at
- Anything trivially recoverable from the codebase or next message
- Restatements of what was just done
- General knowledge

`kind` must be one of: user, project, preference, fact.
Each `text` must be a complete self-contained sentence — readable later with no other context. Do NOT use placeholder text or angle-bracket fragments.

EXAMPLE conversation:
user: I'm Sarah, a backend engineer at Acme Corp, mostly Go.
assistant: Got it.
user: We're cutting the v2 API release on Friday — no breaking changes after that.
assistant: Understood.

EXAMPLE output:
{"candidates": [
  {"text": "Sarah is a backend engineer at Acme Corp and primarily writes Go.", "kind": "user", "importance": 5, "rationale": "stable identity and stack"},
  {"text": "The Acme v2 API release is on Friday; no breaking changes accepted after that.", "kind": "project", "importance": 4, "rationale": "hard deadline and constraint"}
]}

If nothing meets the bar, return: {"candidates": []}

Now process the actual conversation. Respond with ONLY the JSON object, no prose, no markdown fences."""


class MemoryCandidate(BaseModel):
    text: str
    kind: str = "fact"
    importance: int = 3
    rationale: str = ""


class ExtractionResult(BaseModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list)


def _extract_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object")
    return json.loads(m.group(0))


def _cosine(a: list[float], b: list[float]) -> float:
    da = math.sqrt(sum(x * x for x in a)) or 1.0
    db = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (da * db)


async def extract_candidates(
    provider: Provider,
    model: str,
    messages: list[Message],
    *,
    window: int = 12,
) -> list[MemoryCandidate]:
    pertinent = [m for m in messages if m.role in ("user", "assistant")][-window:]
    if not pertinent:
        return []
    convo = "\n".join(f"{m.role}: {m.content}" for m in pertinent)
    req = ChatRequest(
        model=model,
        messages=[
            Message(role="system", content=EXTRACT_PROMPT),
            Message(role="user", content=convo[:8000]),
        ],
        temperature=0.1,
    )
    try:
        resp = await provider.chat(req)
        data = _extract_json(resp.message.content)
        return [MemoryCandidate(**c) for c in data.get("candidates", []) if c.get("text")]
    except Exception:
        return []
