"""Default tool registry for the agent.

Brings together file-search (glob, grep), file-ops (read, edit, multi_edit,
write), exec (shell, python), and web (fetch) tools — plus a meta tool
(``tool_search``) that lets the planner fetch full schemas on demand instead
of paying the schema-token cost up-front for every tool.

All file-touching tools route through the registry's ``invalidate_path`` hook
so the result cache and read-stamps stay coherent after writes.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import textwrap
from pathlib import Path

import httpx

from ..config import ToolPolicy
from .code_search import glob_tool, grep_tool
from .file_ops import edit_tool, multi_edit_tool, read_tool, safe_resolve, write_tool
from .registry import ToolRegistry, ToolResult, ToolSpec


def build_default_registry(policy: ToolPolicy) -> ToolRegistry:
    reg = ToolRegistry()
    reg.workspace_key = str(Path(policy.workspace).resolve())

    def _on_change(p: Path) -> None:
        reg.invalidate_path(str(p))

    # ── glob ────────────────────────────────────────────────────
    async def _glob(pattern: str, path: str | None = None, head_limit: int = 200) -> ToolResult:
        return await glob_tool(pattern, path, workspace=policy.workspace, head_limit=head_limit)

    reg.register(
        ToolSpec(
            name="glob",
            description="Find files by glob pattern (e.g. '**/*.py'). Returns paths sorted by mtime, newest first.",
            category="search",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern; supports ** for recursive."},
                    "path": {"type": "string", "description": "Subdirectory to search (defaults to workspace root)."},
                    "head_limit": {"type": "integer", "default": 200},
                },
                "required": ["pattern"],
            },
        ),
        _glob,
    )

    # ── grep ────────────────────────────────────────────────────
    async def _grep(
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        output_mode: str = "files_with_matches",
        case_insensitive: bool = False,
        line_numbers: bool = True,
        before_context: int = 0,
        after_context: int = 0,
        context: int = 0,
        multiline: bool = False,
        head_limit: int = 250,
    ) -> ToolResult:
        return await grep_tool(
            pattern,
            path,
            glob=glob,
            output_mode=output_mode,  # type: ignore[arg-type]
            case_insensitive=case_insensitive,
            line_numbers=line_numbers,
            before_context=before_context,
            after_context=after_context,
            context=context,
            multiline=multiline,
            head_limit=head_limit,
            workspace=policy.workspace,
        )

    reg.register(
        ToolSpec(
            name="grep",
            description=(
                "Search file contents with a regex. output_mode: 'files_with_matches' (default), "
                "'content' (lines+optional context), or 'count'."
            ),
            category="search",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string", "description": "Filter files by glob, e.g. '*.py'."},
                    "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"]},
                    "case_insensitive": {"type": "boolean", "default": False},
                    "line_numbers": {"type": "boolean", "default": True},
                    "before_context": {"type": "integer", "default": 0},
                    "after_context": {"type": "integer", "default": 0},
                    "context": {"type": "integer", "default": 0},
                    "multiline": {"type": "boolean", "default": False},
                    "head_limit": {"type": "integer", "default": 250},
                },
                "required": ["pattern"],
            },
        ),
        _grep,
    )

    # ── read ────────────────────────────────────────────────────
    async def _read(path: str, offset: int = 0, limit: int = 2000) -> ToolResult:
        return await read_tool(path, workspace=policy.workspace, state=reg.state, offset=offset, limit=limit)

    reg.register(
        ToolSpec(
            name="read",
            description="Read a file with cat -n style line numbers. Use offset+limit to page through large files.",
            category="file",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "default": 0, "description": "0-indexed line to start at."},
                    "limit": {"type": "integer", "default": 2000},
                },
                "required": ["path"],
            },
        ),
        _read,
    )

    # ── file_read (alias kept for back-compat with v0.1 prompts) ─
    async def _file_read(path: str, max_bytes: int = 200_000) -> ToolResult:
        # Delegate to the new read tool but truncate to byte budget.
        res = await read_tool(path, workspace=policy.workspace, state=reg.state, offset=0, limit=10_000)
        if res.ok and len(res.output) > max_bytes:
            res = ToolResult(ok=True, output=res.output[:max_bytes] + "\n…[truncated]", meta=res.meta)
        return res

    reg.register(
        ToolSpec(
            name="file_read",
            description="(Alias for read) Read a UTF-8 text file from the workspace.",
            category="file",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_bytes": {"type": "integer", "default": 200000},
                },
                "required": ["path"],
            },
        ),
        _file_read,
    )

    # ── write ───────────────────────────────────────────────────
    async def _write(path: str, content: str, append: bool = False) -> ToolResult:
        if not policy.allow_file_write:
            return ToolResult(ok=False, error="file_write disabled by policy")
        if append:
            try:
                p = safe_resolve(policy.workspace, path)
            except PermissionError as e:
                return ToolResult(ok=False, error=str(e))
            existing = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
            content = existing + content
        return await write_tool(path, content, workspace=policy.workspace, state=reg.state, on_change=_on_change)

    reg.register(
        ToolSpec(
            name="file_write",
            description="Write text to a file in the workspace (full overwrite). Returns a unified diff vs prior content.",
            category="file",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "append": {"type": "boolean", "default": False},
                },
                "required": ["path", "content"],
            },
        ),
        _write,
    )

    # ── edit ────────────────────────────────────────────────────
    async def _edit(path: str, old_string: str, new_string: str, replace_all: bool = False) -> ToolResult:
        if not policy.allow_file_write:
            return ToolResult(ok=False, error="edit disabled by policy (file_write off)")
        return await edit_tool(
            path, old_string, new_string,
            workspace=policy.workspace, state=reg.state,
            replace_all=replace_all, on_change=_on_change,
        )

    reg.register(
        ToolSpec(
            name="edit",
            description=(
                "Replace old_string with new_string in a file. Requires that you `read` the file first. "
                "old_string must be unique in the file unless replace_all=true."
            ),
            category="file",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean", "default": False},
                },
                "required": ["path", "old_string", "new_string"],
            },
        ),
        _edit,
    )

    # ── multi_edit ──────────────────────────────────────────────
    async def _multi_edit(path: str, edits: list[dict]) -> ToolResult:
        if not policy.allow_file_write:
            return ToolResult(ok=False, error="multi_edit disabled by policy (file_write off)")
        return await multi_edit_tool(
            path, edits,
            workspace=policy.workspace, state=reg.state, on_change=_on_change,
        )

    reg.register(
        ToolSpec(
            name="multi_edit",
            description=(
                "Apply a list of edits sequentially to one file. Each edit is "
                "{old_string,new_string,replace_all?}. All-or-nothing: any failure aborts."
            ),
            category="file",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_string": {"type": "string"},
                                "new_string": {"type": "string"},
                                "replace_all": {"type": "boolean", "default": False},
                            },
                            "required": ["old_string", "new_string"],
                        },
                    },
                },
                "required": ["path", "edits"],
            },
        ),
        _multi_edit,
    )

    # ── shell_exec ──────────────────────────────────────────────
    async def shell_exec(command: str, cwd: str | None = None) -> ToolResult:
        if not policy.allow_shell:
            return ToolResult(ok=False, error="shell_exec disabled by policy (set tools.allow_shell=true)")
        try:
            wd = safe_resolve(policy.workspace, cwd or ".")
        except PermissionError as e:
            return ToolResult(ok=False, error=str(e))
        try:
            proc = await asyncio.create_subprocess_shell(
                command, cwd=str(wd),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=policy.shell_timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                return ToolResult(ok=False, error=f"timeout after {policy.shell_timeout_s}s")
            output = (out.decode("utf-8", "replace") + err.decode("utf-8", "replace"))[:50_000]
            # Shell may have written files — drop file caches conservatively.
            reg.invalidate_path()
            return ToolResult(ok=proc.returncode == 0, output=output, meta={"returncode": proc.returncode})
        except Exception as e:
            return ToolResult(ok=False, error=str(e))

    reg.register(
        ToolSpec(
            name="shell_exec",
            description="Run a shell command in the workspace. Disabled by default; requires tools.allow_shell.",
            category="exec",
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string"}, "cwd": {"type": "string"}},
                "required": ["command"],
            },
        ),
        shell_exec,
    )

    # ── python_exec ─────────────────────────────────────────────
    async def python_exec(code: str) -> ToolResult:
        if not policy.allow_python_exec:
            return ToolResult(ok=False, error="python_exec disabled by policy (set tools.allow_python_exec=true)")
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(textwrap.dedent(code))
            script = f.name
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", script,
                cwd=str(policy.workspace),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=policy.python_timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                return ToolResult(ok=False, error=f"timeout after {policy.python_timeout_s}s")
            output = (out.decode("utf-8", "replace") + err.decode("utf-8", "replace"))[:50_000]
            return ToolResult(ok=proc.returncode == 0, output=output, meta={"returncode": proc.returncode})
        finally:
            try:
                Path(script).unlink()
            except OSError:
                pass

    reg.register(
        ToolSpec(
            name="python_exec",
            description="Execute Python code in an isolated subprocess. Disabled by default.",
            category="exec",
            parameters={
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        ),
        python_exec,
    )

    # ── web_fetch ───────────────────────────────────────────────
    async def web_fetch(url: str, max_bytes: int = 200_000) -> ToolResult:
        if not policy.allow_web_fetch:
            return ToolResult(ok=False, error="web_fetch disabled by policy")
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(url, headers={"user-agent": "LocalAgent/0.3"})
            r.raise_for_status()
            text = r.text[:max_bytes]
            if "<html" in text.lower()[:1000]:
                from ..rag.ingest import _html_to_text
                text = _html_to_text(text)
            return ToolResult(ok=True, output=text, meta={"url": str(r.url), "status": r.status_code})

    reg.register(
        ToolSpec(
            name="web_fetch",
            description="Fetch a URL and return its text content (HTML stripped).",
            category="web",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}, "max_bytes": {"type": "integer", "default": 200000}},
                "required": ["url"],
            },
        ),
        web_fetch,
    )

    # ── tool_search (meta) ──────────────────────────────────────
    async def tool_search(query: str) -> ToolResult:
        """Look up the full JSON schema(s) for tool(s) matching ``query``.

        Two query forms (mirrors Claude Code's ToolSearch):
        * ``select:name1,name2`` — exact lookup
        * ``free text`` — substring search across name + description

        Returns JSON: ``{"tools": [{...full spec...}]}``.
        """
        q = query.strip()
        if q.startswith("select:"):
            wanted = {n.strip() for n in q[len("select:"):].split(",") if n.strip()}
            specs = [s for s in reg.specs() if s.name in wanted]
        else:
            ql = q.lower()
            specs = [
                s for s in reg.specs()
                if ql in s.name.lower() or ql in s.description.lower()
            ][:8]
        payload = {"tools": [s.model_dump() for s in specs]}
        return ToolResult(
            ok=True,
            output=json.dumps(payload, indent=2),
            meta={"matched": len(specs)},
        )

    reg.register(
        ToolSpec(
            name="tool_search",
            description=(
                "Look up full JSON schemas for tools by name or keyword. Use this when the compact "
                "catalog isn't enough to know how to call a tool. Forms: 'select:name1,name2' or free text."
            ),
            category="meta",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
        tool_search,
    )

    return reg
