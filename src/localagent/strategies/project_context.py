"""Project-context loader — auto-discovers LOCALAGENT.md / AGENTS.md in the workspace.

Mirrors Claude Code's ``CLAUDE.md`` mechanism. On every prompt prep, walk
upward from the workspace root looking for a project-context file (in
priority order: ``LOCALAGENT.md``, ``AGENTS.md``, ``.localagent/CONTEXT.md``).
The first one found is hot-read and concatenated as a synthetic strategy block
for whatever scope is being composed.

The file is plain markdown — no frontmatter required — so users can drop
project-specific instructions in alongside README.md without learning the
strategy format.
"""
from __future__ import annotations

from pathlib import Path

# Priority-ordered. First match wins.
_FILENAMES: tuple[str, ...] = (
    "LOCALAGENT.md",
    ".localagent/CONTEXT.md",
    "AGENTS.md",
    ".agents.md",
)

_MAX_BYTES: int = 64_000  # cap so a runaway file doesn't blow up the prompt


def discover_project_context(start: Path, *, ceiling: Path | None = None) -> tuple[Path, str] | None:
    """Walk upward from ``start`` looking for a project-context file.

    Stops at filesystem root or at ``ceiling`` (exclusive). Returns the
    (path, contents) of the first match, or None.
    """
    start = start.resolve()
    if start.is_file():
        start = start.parent
    cur = start
    seen: set[Path] = set()
    while cur not in seen:
        seen.add(cur)
        for name in _FILENAMES:
            p = cur / name
            if p.exists() and p.is_file():
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if len(text) > _MAX_BYTES:
                    text = text[:_MAX_BYTES] + "\n…[truncated]"
                return p, text
        if ceiling is not None and cur == ceiling:
            break
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def format_project_context(path: Path, body: str) -> str:
    """Wrap a discovered context file as a master_context-style block."""
    body = body.strip()
    if not body:
        return ""
    return (
        "<project_context>\n"
        f"### Project context (from {path.name})\n"
        f"{body}\n"
        "</project_context>"
    )
