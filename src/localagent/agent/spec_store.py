"""Persist Specs to ``~/.localagent/specs/<id>.json``.

The orchestrator calls ``store.save(spec)`` after every phase transition and
every chunk completion so a Ctrl-C never loses more than one phase. Reads
are cheap; we re-parse on every access to allow hand-edits between sessions.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .spec import Spec


class SpecStore:
    def __init__(self, dir_path: Path):
        self.dir = Path(dir_path)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, sid: str) -> Path:
        return self.dir / f"{sid}.json"

    def save(self, spec: Spec) -> Spec:
        spec.updated_at = time.time()
        path = self._path(spec.id)
        path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        return spec

    def load(self, sid: str) -> Spec | None:
        p = self._path(sid)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        try:
            return Spec(**data)
        except Exception:
            return None

    def delete(self, sid: str) -> bool:
        p = self._path(sid)
        if p.exists():
            p.unlink()
            return True
        return False

    def list(self) -> list[dict]:
        """Return [{id, title, status, updated_at, chunks}] sorted by updated_at desc."""
        rows: list[dict] = []
        for p in self.dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rows.append({
                "id": data.get("id", p.stem),
                "title": data.get("title", ""),
                "status": data.get("status", "draft"),
                "updated_at": data.get("updated_at", 0),
                "chunks": len(data.get("work_chunks", [])),
                "rounds": data.get("rounds", 0),
            })
        rows.sort(key=lambda r: r["updated_at"], reverse=True)
        return rows
