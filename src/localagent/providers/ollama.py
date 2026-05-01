from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator

import httpx

from ..core import ChatChunk, ChatRequest, ChatResponse, Message, Provider, ToolCall, Usage


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    def _payload(self, req: ChatRequest, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": req.model,
            "messages": [self._msg_to_ollama(m) for m in req.messages],
            "stream": stream,
            "options": {"temperature": req.temperature, **req.extra.get("options", {})},
        }
        if req.max_tokens is not None:
            body["options"]["num_predict"] = req.max_tokens
        if req.stop:
            body["options"]["stop"] = req.stop
        if req.tools:
            body["tools"] = req.tools
        return body

    @staticmethod
    def _msg_to_ollama(m: Message) -> dict[str, Any]:
        d: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            d["tool_calls"] = [
                {"function": {"name": tc.name, "arguments": tc.arguments}} for tc in m.tool_calls
            ]
        return d

    @staticmethod
    def _parse_tool_calls(raw: list[dict[str, Any]] | None) -> list[ToolCall]:
        if not raw:
            return []
        out = []
        for tc in raw:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            out.append(ToolCall(id=tc.get("id") or str(uuid.uuid4()), name=fn.get("name", ""), arguments=args))
        return out

    async def chat(self, req: ChatRequest) -> ChatResponse:
        t0 = time.perf_counter()
        r = await self._client.post("/api/chat", json=self._payload(req, stream=False))
        r.raise_for_status()
        data = r.json()
        msg_data = data.get("message", {})
        msg = Message(
            role="assistant",
            content=msg_data.get("content", ""),
            tool_calls=self._parse_tool_calls(msg_data.get("tool_calls")),
        )
        usage = Usage(
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            total_tokens=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
        return ChatResponse(message=msg, usage=usage, raw=data)

    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatChunk]:
        t0 = time.perf_counter()
        async with self._client.stream("POST", "/api/chat", json=self._payload(req, stream=True)) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                msg = data.get("message", {})
                if data.get("done"):
                    yield ChatChunk(
                        done=True,
                        usage=Usage(
                            prompt_tokens=data.get("prompt_eval_count", 0),
                            completion_tokens=data.get("eval_count", 0),
                            total_tokens=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
                            latency_ms=(time.perf_counter() - t0) * 1000,
                        ),
                    )
                else:
                    yield ChatChunk(
                        delta=msg.get("content", ""),
                        tool_calls=self._parse_tool_calls(msg.get("tool_calls")),
                    )

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        r = await self._client.post("/api/embed", json={"model": model, "input": inputs})
        r.raise_for_status()
        return r.json().get("embeddings", [])

    async def list_models(self) -> list[str]:
        r = await self._client.get("/api/tags")
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
