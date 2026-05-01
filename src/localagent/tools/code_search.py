"""Glob and Grep tools — code-search primitives.

Both run in pure Python (no shell), respect the workspace boundary, and
default to skipping noisy directories (.git, node_modules, .venv, __pycache__,
dist, build). Output formats mirror Claude Code's: glob returns paths sorted
by mtime descending; grep supports content/files_with_matches/count modes
with optional context lines and multi-line patterns.
"""
from __future__ import annotations

import os
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable, Literal

from .registry import ToolResult

# Directories we never recurse into during glob/grep — they're almost always noise
# and on Windows they slow scans drastically.
_DEFAULT_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", ".venv", "venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "dist", "build", "target", ".next", ".nuxt",
    ".idea", ".vscode",
})

_BINARY_EXT: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".class",
    ".pyc", ".pyo", ".o", ".a",
    ".mp3", ".mp4", ".wav", ".mov", ".avi",
    ".db", ".sqlite", ".sqlite3",
})


def _safe_resolve(workspace: Path, path: str | None) -> Path:
    """Resolve ``path`` relative to ``workspace`` and refuse traversal."""
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    if not path:
        return workspace
    p = Path(path)
    p = (workspace / p).resolve() if not p.is_absolute() else p.resolve()
    if not str(p).startswith(str(workspace)):
        raise PermissionError(f"path escapes workspace: {p}")
    return p


def _walk(root: Path, *, skip_dirs: Iterable[str] = _DEFAULT_SKIP_DIRS):
    """Walk ``root`` yielding files; prune noisy dirs early."""
    skip = set(skip_dirs)
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        # mutate dirnames in-place to prune the walk
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".") or d in {".github"}]
        for fn in filenames:
            yield Path(dirpath) / fn


def _matches_pattern(path: Path, pattern: str, root: Path) -> bool:
    """Match ``path`` against a glob pattern relative to ``root``.

    Supports ``**`` recursive matches with the standard "zero-or-more dirs"
    semantics — ``**/*.py`` matches both ``a.py`` and ``sub/b.py``.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    rel_str = str(rel).replace("\\", "/")
    norm_pat = pattern.replace("\\", "/")
    if "**" not in norm_pat:
        return fnmatch(rel_str, norm_pat)
    regex = "^" + _glob_to_regex(norm_pat) + "$"
    return re.match(regex, rel_str) is not None


def _glob_to_regex(pat: str) -> str:
    """Convert a glob with ``**`` to a regex.

    Handles the special case ``**/X`` so that X at the root also matches
    (zero directory levels), which is the conventional shell-glob behavior.
    """
    parts = pat.split("/")
    out: list[str] = []
    for i, piece in enumerate(parts):
        if piece == "**":
            # `**` followed by another piece: match zero or more dir levels.
            if i + 1 < len(parts):
                # Append "(?:.*/)?" — and skip the next slash join below.
                out.append("(?:[^/]+/)*")
                continue
            else:
                out.append(".*")
                continue
        # Translate this single segment.
        out.append(_glob_segment_regex(piece))
    # Stitch with "/" but elide the slash AFTER any "(?:[^/]+/)*" because
    # that group already includes the separator.
    pieces: list[str] = []
    for i, frag in enumerate(out):
        pieces.append(frag)
        if i < len(out) - 1 and not frag.endswith("/)*"):
            pieces.append("/")
    return "".join(pieces)


def _glob_segment_regex(piece: str) -> str:
    """Translate a single glob segment (no slashes) to a regex."""
    out: list[str] = []
    i = 0
    while i < len(piece):
        c = piece[i]
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == ".":
            out.append("\\.")
        elif c in "+()[]{}|^$\\":
            out.append("\\" + c)
        else:
            out.append(c)
        i += 1
    return "".join(out)


# ── glob ────────────────────────────────────────────────────


async def glob_tool(
    pattern: str,
    path: str | None = None,
    *,
    workspace: Path,
    head_limit: int = 200,
) -> ToolResult:
    """Find files by glob pattern. Returns paths sorted by mtime (newest first)."""
    try:
        root = _safe_resolve(workspace, path)
    except PermissionError as e:
        return ToolResult(ok=False, error=str(e))
    if not root.exists():
        return ToolResult(ok=False, error=f"path not found: {root}")

    # Hard ceiling so a runaway tree can't blow up RAM, but high enough that
    # the mtime sort below is meaningful for any realistic repo.
    HARD_CEILING = 5000
    matches: list[Path] = []
    for p in _walk(root):
        if _matches_pattern(p, pattern, root):
            matches.append(p)
            if len(matches) >= HARD_CEILING:
                break

    matches.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    truncated = len(matches) > head_limit
    matches = matches[:head_limit]

    # Format paths relative to workspace for readable output
    ws = workspace.resolve()
    lines: list[str] = []
    for p in matches:
        try:
            rel = p.relative_to(ws)
            lines.append(str(rel).replace("\\", "/"))
        except ValueError:
            lines.append(str(p).replace("\\", "/"))
    output = "\n".join(lines) if lines else "(no matches)"
    return ToolResult(
        ok=True,
        output=output,
        meta={"count": len(matches), "truncated": truncated, "root": str(root)},
    )


# ── grep ────────────────────────────────────────────────────


GrepMode = Literal["content", "files_with_matches", "count"]


async def grep_tool(
    pattern: str,
    path: str | None = None,
    *,
    glob: str | None = None,
    output_mode: GrepMode = "files_with_matches",
    case_insensitive: bool = False,
    line_numbers: bool = True,
    before_context: int = 0,
    after_context: int = 0,
    context: int = 0,
    multiline: bool = False,
    head_limit: int = 250,
    workspace: Path,
) -> ToolResult:
    """Search file contents with a regex. Pure-Python ripgrep-shaped tool."""
    try:
        root = _safe_resolve(workspace, path)
    except PermissionError as e:
        return ToolResult(ok=False, error=str(e))
    if not root.exists():
        return ToolResult(ok=False, error=f"path not found: {root}")

    flags = re.MULTILINE
    if case_insensitive:
        flags |= re.IGNORECASE
    if multiline:
        flags |= re.DOTALL
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        return ToolResult(ok=False, error=f"bad regex: {e}")

    if context:
        before_context = after_context = context

    files_iter: Iterable[Path]
    if root.is_file():
        files_iter = [root]
    else:
        files_iter = _walk(root)

    files_with: list[str] = []
    counts: dict[str, int] = {}
    content_blocks: list[str] = []
    total_lines = 0
    truncated = False
    ws = workspace.resolve()

    for p in files_iter:
        if p.suffix.lower() in _BINARY_EXT:
            continue
        if glob is not None and not _matches_pattern(p, glob, root if root.is_dir() else ws):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue

        rel = _relpath(p, ws)
        if multiline:
            # whole-file scan
            file_matches = list(rx.finditer(text))
            if not file_matches:
                continue
            files_with.append(rel)
            counts[rel] = len(file_matches)
            if output_mode == "content":
                # report each match with the line it starts on
                for m in file_matches:
                    start_line = text.count("\n", 0, m.start()) + 1
                    snippet = m.group(0)
                    prefix = f"{rel}:{start_line}:" if line_numbers else f"{rel}:"
                    content_blocks.append(f"{prefix}{snippet}")
                    total_lines += 1
                    if total_lines >= head_limit:
                        truncated = True
                        break
        else:
            lines = text.splitlines()
            hits: list[int] = [i for i, ln in enumerate(lines) if rx.search(ln)]
            if not hits:
                continue
            files_with.append(rel)
            counts[rel] = len(hits)
            if output_mode == "content":
                printed: set[int] = set()
                for hit in hits:
                    start = max(0, hit - before_context)
                    end = min(len(lines), hit + after_context + 1)
                    if printed and min(printed) > end:
                        # gap separator
                        content_blocks.append("--")
                    for i in range(start, end):
                        if i in printed:
                            continue
                        printed.add(i)
                        sep = ":" if i == hit else "-"
                        prefix = f"{rel}{sep}{i + 1}{sep}" if line_numbers else f"{rel}{sep}"
                        content_blocks.append(f"{prefix}{lines[i]}")
                        total_lines += 1
                        if total_lines >= head_limit:
                            truncated = True
                            break
                    if truncated:
                        break
        if truncated:
            break
        if output_mode != "content" and len(files_with) >= head_limit:
            truncated = True
            break

    if output_mode == "files_with_matches":
        body = "\n".join(files_with[:head_limit]) if files_with else "(no matches)"
    elif output_mode == "count":
        body = "\n".join(f"{f}:{counts[f]}" for f in files_with[:head_limit]) if files_with else "(no matches)"
    else:
        body = "\n".join(content_blocks) if content_blocks else "(no matches)"

    return ToolResult(
        ok=True,
        output=body,
        meta={
            "files_matched": len(files_with),
            "total_matches": sum(counts.values()),
            "truncated": truncated,
            "mode": output_mode,
        },
    )


def _relpath(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")
