from __future__ import annotations

import json
import re

from ..config import ModelRoles
from ..core import ChatRequest, Message, Provider

CLASSIFIER_PROMPT = """You classify a user's request into one role. Output strict JSON.

Roles:
- "code": programming, debugging, code review, syntax/library questions, file/repo manipulation.
- "chat": general conversation, writing, summarization, factual Q&A, anything not code.

Respond with ONLY: {"role": "code"} or {"role": "chat"}. No prose."""


class Router:
    """Maps logical roles → concrete model names. With classifier-routed `auto` role."""

    def __init__(self, roles: ModelRoles, provider: Provider | None = None):
        self.roles = roles
        self.provider = provider

    def resolve_static(self, role_or_model: str | None) -> str:
        if not role_or_model or role_or_model == "auto":
            return self.roles.chat
        mapping = self.roles.model_dump()
        return mapping.get(role_or_model, role_or_model)

    async def classify(self, user_text: str) -> str:
        """Run the tiny classifier model. Returns 'code' or 'chat'."""
        if self.provider is None:
            return "chat"
        req = ChatRequest(
            model=self.roles.router,
            messages=[
                Message(role="system", content=CLASSIFIER_PROMPT),
                Message(role="user", content=user_text[:2000]),
            ],
            temperature=0.0,
            max_tokens=20,
        )
        try:
            resp = await self.provider.chat(req)
            txt = resp.message.content.strip()
            m = re.search(r"\{.*\}", txt, re.S)
            if m:
                data = json.loads(m.group(0))
                role = data.get("role", "chat")
                return role if role in {"code", "chat"} else "chat"
        except Exception:
            pass
        return "chat"

    async def route(self, role_or_auto: str | None, user_text: str) -> str:
        if role_or_auto == "auto":
            role = await self.classify(user_text)
            return self.resolve_static(role)
        return self.resolve_static(role_or_auto)
