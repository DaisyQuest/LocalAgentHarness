from __future__ import annotations

import json
import re

from ..core import ChatRequest, Message, Provider

EXTRACT_PROMPT = """Extract durable facts about the user or the project from this conversation that
would be useful in FUTURE conversations. Skip ephemeral details. Output strict JSON:
{"memories": [{"text": "<fact>", "kind": "user|project|preference|fact"}]}

If nothing notable, output {"memories": []}."""


async def extract_memories(provider: Provider, model: str, conversation: list[Message]) -> list[dict[str, str]]:
    convo = "\n".join(f"{m.role}: {m.content}" for m in conversation if m.role in ("user", "assistant"))
    if not convo.strip():
        return []
    req = ChatRequest(
        model=model,
        messages=[
            Message(role="system", content=EXTRACT_PROMPT),
            Message(role="user", content=convo[:8000]),
        ],
        temperature=0.0,
    )
    try:
        resp = await provider.chat(req)
        m = re.search(r"\{.*\}", resp.message.content, re.S)
        if not m:
            return []
        data = json.loads(m.group(0))
        return [x for x in data.get("memories", []) if x.get("text")]
    except Exception:
        return []
