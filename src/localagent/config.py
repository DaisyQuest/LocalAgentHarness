from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `over` into `base`. Returns a new dict; inputs untouched."""
    out = dict(base)
    for k, v in over.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class ProviderConfig(BaseModel):
    kind: Literal["ollama", "openai_compat"] = "ollama"
    base_url: str = "http://localhost:11434"
    api_key: str | None = None


class ModelRoles(BaseModel):
    chat: str = "llama3.1:8b"
    code: str = "qwen2.5-coder:7b"
    fast: str = "llama3.2:1b"
    router: str = "llama3.2:1b"
    planner: str = "llama3.1:8b"
    executor: str = "qwen2.5-coder:7b"
    memory_extractor: str = "llama3.2:1b"
    embed: str = "nomic-embed-text"


class RagConfig(BaseModel):
    chunk_size: int = 800
    chunk_overlap: int = 100
    top_k: int = 5
    embed_dim: int = 768


class ToolPolicy(BaseModel):
    allow_shell: bool = False
    allow_python_exec: bool = False
    allow_file_write: bool = True
    allow_web_fetch: bool = True
    workspace: Path = Field(default_factory=lambda: Path.home() / ".localagent" / "workspace")
    shell_timeout_s: float = 30.0
    python_timeout_s: float = 30.0
    require_confirmation: list[str] = Field(default_factory=lambda: ["shell_exec", "python_exec"])


class AgentConfig(BaseModel):
    max_steps: int = 8
    use_classifier_router: bool = True
    verbose_steps: bool = True
    # Meta-cognition: extra LLM passes that lift weaker models toward Claude-style discipline.
    use_reframe: bool = True       # restate goal, surface assumptions, flag ambiguity
    use_critique: bool = True      # critic pass on the plan; one revision budget
    use_done_check: bool = True    # verify each criterion at synthesis time
    json_retries: int = 2          # retry budget when sub-LLMs emit malformed JSON
    ambiguity_threshold: int = 4   # 1-5; if reframe scores higher, agent asks instead of guessing


class MemoryConfig(BaseModel):
    backend: Literal["sqlite_vec", "pgvector"] = "sqlite_vec"
    pg_dsn: str | None = None  # postgresql://user:pw@host/db when backend == pgvector
    auto_recall: bool = True
    recall_k: int = 4
    auto_save: bool = True               # run significance extractor in background
    auto_save_every_turns: int = 4       # cadence (counts user turns)
    auto_save_min_importance: int = 3    # 1–5, drop below this threshold
    auto_save_dedup_threshold: float = 0.86  # cosine sim above which a candidate is treated as duplicate
    auto_save_window: int = 12           # how many recent messages to feed the extractor


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LOCALAGENT_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
    )

    data_dir: Path = Field(default_factory=lambda: Path.home() / ".localagent")
    provider: ProviderConfig = ProviderConfig()
    models: ModelRoles = ModelRoles()
    rag: RagConfig = RagConfig()
    tools: ToolPolicy = ToolPolicy()
    agent: AgentConfig = AgentConfig()
    memory: MemoryConfig = MemoryConfig()
    default_role: str = "auto"
    system_prompt: str = "You are a helpful, concise assistant running locally on the user's machine."

    @property
    def db_path(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir / "localagent.db"

    @property
    def overrides_path(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir / "settings.local.json"

    @classmethod
    def load(cls) -> "Settings":
        """Load env+.env, then layer on persisted UI overrides if present."""
        base = cls()
        path = base.overrides_path
        if path.exists():
            try:
                overrides = json.loads(path.read_text(encoding="utf-8"))
                merged = _deep_merge(base.model_dump(mode="json"), overrides)
                return cls(**merged)
            except Exception:
                # if persisted file is corrupt, fall back to env-only
                pass
        return base

    def save_overrides(self, patch: dict[str, Any]) -> None:
        """Merge ``patch`` into the persisted overrides file. Does NOT mutate self."""
        path = self.overrides_path
        existing: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        merged = _deep_merge(existing, patch)
        path.write_text(json.dumps(merged, indent=2, default=str), encoding="utf-8")


settings = Settings.load()
