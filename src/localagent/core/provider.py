from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field

from .messages import Message, ToolCall, Usage


class ChatRequest(BaseModel):
    model: str
    messages: list[Message]
    temperature: float = 0.7
    max_tokens: int | None = None
    stop: list[str] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    message: Message
    usage: Usage = Usage()
    raw: dict[str, Any] = Field(default_factory=dict)


class ChatChunk(BaseModel):
    """One streaming delta."""
    delta: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    done: bool = False
    usage: Usage | None = None


class Provider(ABC):
    """Abstract inference backend. Implementations: Ollama, OpenAI-compatible, llama.cpp, ..."""

    name: str = "abstract"

    @abstractmethod
    async def chat(self, req: ChatRequest) -> ChatResponse: ...

    @abstractmethod
    def stream(self, req: ChatRequest) -> AsyncIterator[ChatChunk]: ...

    @abstractmethod
    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]: ...

    async def list_models(self) -> list[str]:
        return []

    async def close(self) -> None:
        return None
