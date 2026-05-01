"""File-operation tools — Read, Edit, MultiEdit.

These mirror Claude Code's contracts so a smaller local model that's been
prompted to use them gets the same guardrails:

* ``read`` returns ``cat -n``-formatted output and stamps a "you've now
  read this file" mark on the registry's session state.
* ``edit`` requires a prior ``read`` of the same path, refuses non-unique
  ``old_string`` matches unless ``replace_all=True``, normalizes CRLF/LF
  before matching so model-emitted strings (always LF) match Windows files,
  and returns a unified diff of what actually changed.
* ``multi_edit`` applies a list of edits sequentially; later edits see the
  output of earlier ones. All-or-nothing: if any edit fails, the file is
  not written.
"""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from .registry import ToolResult


# ── path helpers ────────────────────────────────────────────


def safe_resolve(workspace: Path, path: str) -> Path:
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    p = Path(path)
    p = (workspace / p).resolve() if not p.is_absolute() else p.resolve()
    if not str(p).startswith(str(workspace)):
        raise PermissionError(f"path escapes workspace: {p}")
    return p


def _normalize_newlines(s: str) -> str:
    """Normalize CRLF/CR to LF for matching purposes."""
    return s.replace("\r\n", "\n").replace("\r", "\n")


def _detect_eol(s: str) -> str:
    """Detect dominant line ending in ``s`` (returns '\\n' or '\\r\\n')."""
    crlf = s.count("\r\n")
    lf = s.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _format_with_line_numbers(text: str, *, start: int = 1, max_line_len: int = 2000) -> str:
    """Render ``text`` as ``cat -n`` output starting at line ``start``."""
    lines = text.split("\n")
    width = max(6, len(str(start + len(lines))))
    out: list[str] = []
    for i, ln in enumerate(lines):
        n = start + i
        if len(ln) > max_line_len:
            ln = ln[:max_line_len] + " …[truncated]"
        out.append(f"{n:>{width}}\t{ln}")
    return "\n".join(out)


# ── read ────────────────────────────────────────────────────


async def read_tool(
    path: str,
    *,
    workspace: Path,
    state: dict[str, Any],
    offset: int = 0,
    limit: int = 2000,
) -> ToolResult:
    """Read a UTF-8 file with line numbers. Stamps state['reads'][path] = mtime."""
    try:
        p = safe_resolve(workspace, path)
    except PermissionError as e:
        return ToolResult(ok=False, error=str(e))
    if not p.exists():
        return ToolResult(ok=False, error=f"not found: {p}")
    if p.is_dir():
        return ToolResult(ok=False, error=f"is a directory: {p}")
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return ToolResult(ok=False, error=f"read failed: {e}")

    norm = _normalize_newlines(raw)
    all_lines = norm.split("\n")
    total_lines = len(all_lines)
    if offset < 0:
        offset = 0
    if offset >= total_lines and total_lines > 0:
        return ToolResult(
            ok=True,
            output=f"(file has {total_lines} lines; offset {offset} is past end)",
            meta={"path": str(p), "lines": total_lines},
        )
    end = min(total_lines, offset + max(1, limit))
    excerpt = "\n".join(all_lines[offset:end])
    body = _format_with_line_numbers(excerpt, start=offset + 1)
    truncated = end < total_lines
    if truncated:
        body += f"\n… ({total_lines - end} more lines; use offset={end}, limit=… to continue)"

    # Stamp the read so Edit can verify the model has seen this file.
    reads = state.setdefault("reads", {})
    reads[str(p)] = p.stat().st_mtime

    return ToolResult(
        ok=True,
        output=body,
        meta={
            "path": str(p),
            "lines": total_lines,
            "offset": offset,
            "shown": end - offset,
            "truncated": truncated,
        },
    )


# ── write ───────────────────────────────────────────────────


async def write_tool(
    path: str,
    content: str,
    *,
    workspace: Path,
    state: dict[str, Any],
    on_change=None,
) -> ToolResult:
    """Write a file (overwrite). Returns a unified diff vs prior content if any."""
    try:
        p = safe_resolve(workspace, path)
    except PermissionError as e:
        return ToolResult(ok=False, error=str(e))
    p.parent.mkdir(parents=True, exist_ok=True)

    prior = ""
    existed = p.exists()
    if existed:
        try:
            prior = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            prior = ""

    p.write_text(content, encoding="utf-8")

    diff = _short_diff(prior, content, label=str(p)) if existed else f"(created {len(content)} chars)"
    if on_change:
        try:
            on_change(p)
        except Exception:
            pass
    # update read stamp so a follow-up Edit on the just-written file is allowed
    state.setdefault("reads", {})[str(p)] = p.stat().st_mtime
    return ToolResult(
        ok=True,
        output=f"wrote {len(content)} chars to {p}\n\n{diff}",
        meta={"path": str(p), "bytes": len(content), "created": not existed},
    )


# ── edit ────────────────────────────────────────────────────


def _apply_edit(
    text: str,
    old_string: str,
    new_string: str,
    replace_all: bool,
) -> tuple[str, int, str | None]:
    """Apply a single edit. Returns (new_text, n_replacements, error_or_None).

    Matches against newline-normalized text so models emitting LF can edit
    files saved with CRLF.
    """
    if old_string == new_string:
        return text, 0, "old_string and new_string are identical — no-op"
    norm = _normalize_newlines(text)
    norm_old = _normalize_newlines(old_string)
    norm_new = _normalize_newlines(new_string)
    if norm_old == "":
        return text, 0, "old_string is empty"
    count = norm.count(norm_old)
    if count == 0:
        return text, 0, "old_string not found in file"
    if count > 1 and not replace_all:
        return text, 0, (
            f"old_string matches {count} places; pass replace_all=true or expand the "
            "match with more surrounding context to make it unique"
        )
    if replace_all:
        new_norm = norm.replace(norm_old, norm_new)
        n = count
    else:
        new_norm = norm.replace(norm_old, norm_new, 1)
        n = 1
    # Restore original line ending style for the whole file.
    eol = _detect_eol(text)
    if eol == "\r\n":
        new_text = new_norm.replace("\n", "\r\n")
    else:
        new_text = new_norm
    return new_text, n, None


def _short_diff(before: str, after: str, *, label: str = "file", n_context: int = 2) -> str:
    """Return a compact unified diff (CRLF/LF normalized for clarity)."""
    a = _normalize_newlines(before).splitlines(keepends=False)
    b = _normalize_newlines(after).splitlines(keepends=False)
    diff = difflib.unified_diff(a, b, fromfile=f"a/{label}", tofile=f"b/{label}", n=n_context, lineterm="")
    out = list(diff)
    if not out:
        return "(no changes)"
    # Cap at ~120 lines so diffs don't blow up the agent context
    if len(out) > 120:
        out = out[:120] + [f"… (diff truncated; {len(diff) if isinstance(diff, list) else 'more'} more lines)"]
    return "\n".join(out)


async def edit_tool(
    path: str,
    old_string: str,
    new_string: str,
    *,
    workspace: Path,
    state: dict[str, Any],
    replace_all: bool = False,
    require_read: bool = True,
    on_change=None,
) -> ToolResult:
    """Replace ``old_string`` with ``new_string`` in ``path``. Must Read first."""
    try:
        p = safe_resolve(workspace, path)
    except PermissionError as e:
        return ToolResult(ok=False, error=str(e))
    if not p.exists():
        return ToolResult(ok=False, error=f"not found: {p}")
    if p.is_dir():
        return ToolResult(ok=False, error=f"is a directory: {p}")

    if require_read:
        reads = state.get("reads", {})
        last = reads.get(str(p))
        if last is None:
            return ToolResult(
                ok=False,
                error=(
                    f"must Read {p} before editing — call the `read` tool first so you "
                    "see the current file content"
                ),
            )
        # If the file changed on disk since the last read, force a re-read.
        # 1ms epsilon handles filesystems with coarse mtime resolution (FAT,
        # some network shares) without missing real human-scale edits.
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = last
        if mtime - last > 1e-3:
            return ToolResult(
                ok=False,
                error=(
                    f"{p} has changed since you last read it (mtime moved {mtime - last:.1f}s). "
                    "Call `read` again, then retry the edit."
                ),
            )

    try:
        before = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return ToolResult(ok=False, error=f"read failed: {e}")

    new_text, n, err = _apply_edit(before, old_string, new_string, replace_all)
    if err:
        return ToolResult(ok=False, error=err)
    if new_text == before:
        return ToolResult(ok=True, output=f"(no-op: edit produced no change in {p})", meta={"path": str(p), "replacements": 0})

    p.write_text(new_text, encoding="utf-8")
    state.setdefault("reads", {})[str(p)] = p.stat().st_mtime
    diff = _short_diff(before, new_text, label=str(p))

    if on_change:
        try:
            on_change(p)
        except Exception:
            pass

    return ToolResult(
        ok=True,
        output=f"edited {p} ({n} replacement{'s' if n != 1 else ''})\n\n{diff}",
        meta={"path": str(p), "replacements": n},
    )


async def multi_edit_tool(
    path: str,
    edits: list[dict[str, Any]],
    *,
    workspace: Path,
    state: dict[str, Any],
    require_read: bool = True,
    on_change=None,
) -> ToolResult:
    """Apply a list of edits sequentially. All-or-nothing — fails on first error."""
    try:
        p = safe_resolve(workspace, path)
    except PermissionError as e:
        return ToolResult(ok=False, error=str(e))
    if not p.exists():
        return ToolResult(ok=False, error=f"not found: {p}")
    if not edits:
        return ToolResult(ok=False, error="edits list is empty")

    if require_read:
        reads = state.get("reads", {})
        if str(p) not in reads:
            return ToolResult(ok=False, error=f"must Read {p} before editing")

    try:
        before = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return ToolResult(ok=False, error=f"read failed: {e}")

    current = before
    total = 0
    for i, ed in enumerate(edits):
        old = ed.get("old_string")
        new = ed.get("new_string")
        repl_all = bool(ed.get("replace_all", False))
        if old is None or new is None:
            return ToolResult(ok=False, error=f"edit #{i + 1}: missing old_string/new_string")
        current, n, err = _apply_edit(current, old, new, repl_all)
        if err:
            return ToolResult(ok=False, error=f"edit #{i + 1} failed: {err}")
        total += n

    if current == before:
        return ToolResult(ok=True, output="(no changes)", meta={"path": str(p), "replacements": 0})

    p.write_text(current, encoding="utf-8")
    state.setdefault("reads", {})[str(p)] = p.stat().st_mtime
    diff = _short_diff(before, current, label=str(p))
    if on_change:
        try:
            on_change(p)
        except Exception:
            pass

    return ToolResult(
        ok=True,
        output=f"applied {len(edits)} edits to {p} ({total} replacement{'s' if total != 1 else ''})\n\n{diff}",
        meta={"path": str(p), "edits": len(edits), "replacements": total},
    )
