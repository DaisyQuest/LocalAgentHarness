from __future__ import annotations

import hashlib
import inspect
import json
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

ToolCategory = Literal["search", "file", "exec", "web", "meta"]


def _shape_hint(prop: dict[str, Any]) -> str:
    """Render one level of param shape — enough for the planner to call it.

    Examples: ``: str``, ``: int``, ``: [{old_string, new_string, replace_all}]``.
    """
    t = prop.get("type")
    if t == "array":
        item = prop.get("items") or {}
        if isinstance(item, dict) and item.get("type") == "object":
            keys = list((item.get("properties") or {}).keys())
            inner = ", ".join(keys[:6]) if keys else "..."
            return f": [{{{inner}}}]"
        if isinstance(item, dict) and item.get("type"):
            return f": [{item.get('type')}]"
        return ": []"
    if t in ("string", "integer", "number", "boolean"):
        return f": {t[:3]}"  # str / int / num / boo — terse
    if t == "object":
        keys = list((prop.get("properties") or {}).keys())
        inner = ", ".join(keys[:4]) if keys else "..."
        return f": {{{inner}}}"
    return ""


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    category: ToolCategory = "meta"

    def compact_repr(self) -> str:
        """One-line repr used by the planner's compact tool catalog.

        Drops full JSON schema in favor of `(arg1: type, arg2*: type, …)`.
        ``*`` marks required. Arrays of objects render as ``arg: [{k1, k2}]``
        so the planner can infer shape without calling ``tool_search``.
        """
        props = self.parameters.get("properties", {}) if isinstance(self.parameters, dict) else {}
        required = set(self.parameters.get("required", []) if isinstance(self.parameters, dict) else [])
        parts: list[str] = []
        for k, v in props.items():
            label = f"{k}*" if k in required else k
            shape = _shape_hint(v) if isinstance(v, dict) else ""
            parts.append(f"{label}{shape}")
        return f"{self.name}({', '.join(parts)}) — {self.description}"


class ToolResult(BaseModel):
    ok: bool
    output: str = ""
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


ToolFn = Callable[..., Any | Awaitable[Any]]
Confirmer = Callable[[str, dict[str, Any]], Awaitable[bool]]


class _LRU:
    """Tiny size-bounded LRU. Stores ToolResult-as-dict tuples keyed by str."""

    def __init__(self, capacity: int = 64):
        self.capacity = capacity
        self._d: "OrderedDict[str, tuple[float, ToolResult]]" = OrderedDict()

    def get(self, key: str, ttl_s: float) -> ToolResult | None:
        v = self._d.get(key)
        if v is None:
            return None
        ts, res = v
        if ttl_s > 0 and (time.time() - ts) > ttl_s:
            self._d.pop(key, None)
            return None
        self._d.move_to_end(key)
        return res

    def put(self, key: str, res: ToolResult) -> None:
        self._d[key] = (time.time(), res)
        self._d.move_to_end(key)
        while len(self._d) > self.capacity:
            self._d.popitem(last=False)

    def invalidate_prefix(self, prefix: str) -> int:
        keys = [k for k in self._d if k.startswith(prefix)]
        for k in keys:
            self._d.pop(k, None)
        return len(keys)

    def clear(self) -> None:
        self._d.clear()


class ToolRegistry:
    """Tool registry with session state, LRU result cache, and confirm gating.

    Session state (``self.state``) is a free-form dict tools can read/write
    across calls within one agent run — used for the Edit tool's
    "must Read first" invariant.
    """

    # Tools whose results are deterministic enough to cache within a workspace+args key.
    CACHEABLE: frozenset[str] = frozenset({"file_read", "read", "glob", "grep"})

    def __init__(self, *, cache_capacity: int = 64, cache_ttl_s: float = 60.0) -> None:
        self._tools: dict[str, tuple[ToolSpec, ToolFn]] = {}
        self._confirm_required: set[str] = set()
        self._confirmer: Confirmer | None = None
        self.state: dict[str, Any] = {"reads": {}}  # path -> mtime when last read
        self._cache = _LRU(capacity=cache_capacity)
        self._cache_ttl_s = cache_ttl_s
        self.workspace_key: str = ""  # set by builder; included in cache keys

    def register(self, spec: ToolSpec, fn: ToolFn, *, confirm: bool = False) -> None:
        self._tools[spec.name] = (spec, fn)
        if confirm:
            self._confirm_required.add(spec.name)

    def set_confirmer(self, confirmer: Confirmer | None) -> None:
        self._confirmer = confirmer

    def set_confirm_required(self, names: list[str]) -> None:
        self._confirm_required = set(names)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [s for s, _ in self._tools.values()]

    def get_spec(self, name: str) -> ToolSpec | None:
        t = self._tools.get(name)
        return t[0] if t else None

    def openai_specs(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": s.model_dump(exclude={"category"})} for s in self.specs()]

    def compact_catalog(self) -> str:
        """Compact name(params*) — description list, grouped by category. Cheap on tokens."""
        from itertools import groupby
        specs = sorted(self.specs(), key=lambda s: (s.category, s.name))
        out: list[str] = []
        for cat, group in groupby(specs, key=lambda s: s.category):
            out.append(f"## {cat}")
            for s in group:
                out.append(f"- {s.compact_repr()}")
        return "\n".join(out)

    def invalidate_path(self, path: str | None = None) -> None:
        """Drop cached results that may now be stale.

        Tracking arg→key per path isn't worth the bookkeeping; we wipe all
        cacheable file-read/search entries on any write. ``path`` is used
        only to drop the matching read-stamp so Edit forces a re-read.
        """
        for prefix in ("file_read|", "read|", "grep|", "glob|"):
            self._cache.invalidate_prefix(prefix)
        if path is not None:
            self.state.get("reads", {}).pop(str(path), None)

    def _cache_key(self, name: str, args: dict[str, Any]) -> str:
        try:
            blob = json.dumps(args, sort_keys=True, default=str)
        except Exception:
            blob = repr(sorted(args.items()))
        h = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
        return f"{name}|{self.workspace_key}|{h}"

    async def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        if name not in self._tools:
            return ToolResult(ok=False, error=f"unknown tool: {name}")
        if name in self._confirm_required and self._confirmer is not None:
            try:
                ok = await self._confirmer(name, args)
            except Exception as e:
                return ToolResult(ok=False, error=f"confirmer failed: {e}")
            if not ok:
                return ToolResult(ok=False, error="user denied tool call")

        cache_hit: ToolResult | None = None
        cache_key: str | None = None
        if name in self.CACHEABLE:
            cache_key = self._cache_key(name, args)
            cache_hit = self._cache.get(cache_key, self._cache_ttl_s)
            if cache_hit is not None:
                # Return a copy with a meta marker so callers/UI know it was cached
                cached = cache_hit.model_copy(deep=True)
                cached.meta = {**cached.meta, "cached": True}
                return cached

        _spec, fn = self._tools[name]
        try:
            res = fn(**args)
            if inspect.isawaitable(res):
                res = await res
            if not isinstance(res, ToolResult):
                res = ToolResult(ok=True, output=str(res))
        except Exception as e:
            return ToolResult(ok=False, error=f"{type(e).__name__}: {e}")

        if cache_key is not None and res.ok:
            self._cache.put(cache_key, res)
        return res
