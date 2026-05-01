from __future__ import annotations

from ..config import ProviderConfig
from ..core import Provider
from .ollama import OllamaProvider


def build_provider(cfg: ProviderConfig) -> Provider:
    if cfg.kind == "ollama":
        return OllamaProvider(base_url=cfg.base_url)
    if cfg.kind == "openai_compat":
        from .openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(base_url=cfg.base_url, api_key=cfg.api_key)
    raise ValueError(f"Unknown provider kind: {cfg.kind}")


__all__ = ["build_provider", "OllamaProvider"]
