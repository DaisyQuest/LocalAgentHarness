"""OpenAI-compatible provider (LM Studio, vLLM, llama.cpp server, OpenRouter, ...). Stub."""
from __future__ import annotations

from typing import AsyncIterator

from ..core import ChatChunk, ChatRequest, ChatResponse, Provider


class OpenAICompatProvider(Provider):
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str | None = None):
        self.base_url = base_url
        self.api_key = api_key

    async def chat(self, req: ChatRequest) -> ChatResponse:
        raise NotImplementedError("OpenAI-compatible provider not yet implemented")

    def stream(self, req: ChatRequest) -> AsyncIterator[ChatChunk]:
        raise NotImplementedError

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        raise NotImplementedError
