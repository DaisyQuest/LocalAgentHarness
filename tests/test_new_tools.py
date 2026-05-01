"""Unit tests for the new code-search and file-ops tools.

No LLM dependency — these test the deterministic Python machinery so we can
catch regressions without spinning up the local model.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from localagent.tools.code_search import glob_tool, grep_tool
from localagent.tools.file_ops import (
    _apply_edit,
    _detect_eol,
    _normalize_newlines,
    edit_tool,
    multi_edit_tool,
    read_tool,
    write_tool,
)
from localagent.tools.registry import ToolRegistry, ToolSpec
from localagent.agent.todos import TodoList
from localagent.strategies.project_context import discover_project_context


# ── helpers ─────────────────────────────────────────────────


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if not asyncio.iscoroutine(coro) else asyncio.run(coro)


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


# ── _apply_edit (the heart) ─────────────────────────────────


def test_apply_edit_unique_match_replaces_once():
    text = "hello world\ngoodbye world\n"
    new, n, err = _apply_edit(text, "hello", "HELLO", replace_all=False)
    assert err is None
    assert n == 1
    assert new == "HELLO world\ngoodbye world\n"


def test_apply_edit_non_unique_without_replace_all_errors():
    text = "x = 1\nx = 1\n"
    _, _, err = _apply_edit(text, "x = 1", "x = 2", replace_all=False)
    assert err is not None
    assert "matches 2 places" in err


def test_apply_edit_non_unique_with_replace_all_succeeds():
    text = "x = 1\nx = 1\n"
    new, n, err = _apply_edit(text, "x = 1", "x = 2", replace_all=True)
    assert err is None
    assert n == 2
    assert new == "x = 2\nx = 2\n"


def test_apply_edit_missing_old_string_errors():
    text = "no match here"
    _, _, err = _apply_edit(text, "ZZZ", "AAA", replace_all=False)
    assert err is not None and "not found" in err


def test_apply_edit_identical_strings_noop():
    text = "abc"
    _, _, err = _apply_edit(text, "abc", "abc", replace_all=False)
    assert err is not None and "identical" in err


def test_apply_edit_crlf_match_with_lf_old_string():
    """Model emits LF, file is CRLF. Match must still succeed and preserve CRLF."""
    text = "line1\r\nline2\r\nline3\r\n"
    new, n, err = _apply_edit(text, "line2\n", "LINE2\n", replace_all=False)
    assert err is None
    assert n == 1
    assert new == "line1\r\nLINE2\r\nline3\r\n"
    assert _detect_eol(new) == "\r\n"


def test_apply_edit_lf_file_stays_lf():
    text = "a\nb\nc\n"
    new, _, err = _apply_edit(text, "b", "B", replace_all=False)
    assert err is None
    assert new == "a\nB\nc\n"
    assert "\r\n" not in new


def test_normalize_newlines():
    assert _normalize_newlines("a\r\nb\rc\n") == "a\nb\nc\n"


# ── read tool ───────────────────────────────────────────────


def test_read_returns_line_numbers(tmp_path: Path):
    ws = _ws(tmp_path)
    (ws / "f.py").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    state: dict = {}
    res = asyncio.run(read_tool("f.py", workspace=ws, state=state))
    assert res.ok
    assert "1\talpha" in res.output
    assert "3\tgamma" in res.output
    # read stamps the file
    assert any(k.endswith("f.py") for k in state["reads"])


def test_read_offset_limit(tmp_path: Path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("\n".join(f"line{i}" for i in range(20)), encoding="utf-8")
    state: dict = {}
    res = asyncio.run(read_tool("f.txt", workspace=ws, state=state, offset=5, limit=3))
    assert res.ok
    assert "6\tline5" in res.output
    assert "8\tline7" in res.output
    assert "9\tline8" not in res.output  # limit cuts


def test_read_refuses_path_escape(tmp_path: Path):
    ws = _ws(tmp_path)
    (tmp_path / "outside.txt").write_text("nope", encoding="utf-8")
    state: dict = {}
    res = asyncio.run(read_tool("../outside.txt", workspace=ws, state=state))
    assert not res.ok
    assert "escapes workspace" in res.error


# ── edit tool ───────────────────────────────────────────────


def test_edit_requires_prior_read(tmp_path: Path):
    ws = _ws(tmp_path)
    (ws / "f.py").write_text("abc\n", encoding="utf-8")
    state: dict = {}
    res = asyncio.run(edit_tool("f.py", "abc", "ABC", workspace=ws, state=state))
    assert not res.ok
    assert "must Read" in res.error


def test_edit_after_read_succeeds_with_diff(tmp_path: Path):
    ws = _ws(tmp_path)
    f = ws / "f.py"
    f.write_text("def foo():\n    return 1\n", encoding="utf-8")
    state: dict = {}
    asyncio.run(read_tool("f.py", workspace=ws, state=state))
    res = asyncio.run(
        edit_tool("f.py", "return 1", "return 42", workspace=ws, state=state)
    )
    assert res.ok
    assert "return 42" in f.read_text(encoding="utf-8")
    assert "@@" in res.output  # unified-diff marker


def test_edit_detects_external_modification(tmp_path: Path):
    import time
    ws = _ws(tmp_path)
    f = ws / "f.py"
    f.write_text("a\n", encoding="utf-8")
    state: dict = {}
    asyncio.run(read_tool("f.py", workspace=ws, state=state))
    # Mutate on disk (sleep-free: directly bump mtime)
    time.sleep(0.01)
    f.write_text("b\n", encoding="utf-8")
    res = asyncio.run(edit_tool("f.py", "a", "A", workspace=ws, state=state))
    assert not res.ok
    assert "changed since" in res.error


# ── multi_edit ──────────────────────────────────────────────


def test_multi_edit_applies_in_order(tmp_path: Path):
    ws = _ws(tmp_path)
    f = ws / "f.py"
    f.write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    state: dict = {}
    asyncio.run(read_tool("f.py", workspace=ws, state=state))
    res = asyncio.run(multi_edit_tool(
        "f.py",
        [
            {"old_string": "x = 1", "new_string": "x = 10"},
            {"old_string": "y = 2", "new_string": "y = 20"},
        ],
        workspace=ws, state=state,
    ))
    assert res.ok
    text = f.read_text(encoding="utf-8")
    assert "x = 10" in text and "y = 20" in text


def test_multi_edit_aborts_on_failure(tmp_path: Path):
    ws = _ws(tmp_path)
    f = ws / "f.py"
    original = "x = 1\ny = 2\n"
    f.write_text(original, encoding="utf-8")
    state: dict = {}
    asyncio.run(read_tool("f.py", workspace=ws, state=state))
    res = asyncio.run(multi_edit_tool(
        "f.py",
        [
            {"old_string": "x = 1", "new_string": "x = 10"},
            {"old_string": "DOES_NOT_EXIST", "new_string": "..."},
        ],
        workspace=ws, state=state,
    ))
    assert not res.ok
    # File untouched
    assert f.read_text(encoding="utf-8") == original


# ── glob ────────────────────────────────────────────────────


def test_glob_finds_files(tmp_path: Path):
    ws = _ws(tmp_path)
    (ws / "a.py").write_text("a", encoding="utf-8")
    (ws / "sub").mkdir()
    (ws / "sub" / "b.py").write_text("b", encoding="utf-8")
    (ws / "sub" / "c.txt").write_text("c", encoding="utf-8")
    res = asyncio.run(glob_tool("**/*.py", None, workspace=ws))
    assert res.ok
    assert "a.py" in res.output and "sub/b.py" in res.output
    assert "c.txt" not in res.output


def test_glob_skips_noise_dirs(tmp_path: Path):
    ws = _ws(tmp_path)
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "x.js").write_text("x", encoding="utf-8")
    (ws / "real.js").write_text("y", encoding="utf-8")
    res = asyncio.run(glob_tool("**/*.js", None, workspace=ws))
    assert "real.js" in res.output
    assert "node_modules" not in res.output


# ── grep ────────────────────────────────────────────────────


def test_grep_files_with_matches(tmp_path: Path):
    ws = _ws(tmp_path)
    (ws / "a.py").write_text("def foo():\n    pass\n", encoding="utf-8")
    (ws / "b.py").write_text("def bar():\n    pass\n", encoding="utf-8")
    res = asyncio.run(grep_tool(r"def \w+\(", None, workspace=ws))
    assert res.ok
    assert "a.py" in res.output and "b.py" in res.output


def test_grep_content_with_line_numbers(tmp_path: Path):
    ws = _ws(tmp_path)
    (ws / "f.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    res = asyncio.run(grep_tool("y = ", None, output_mode="content", workspace=ws))
    assert res.ok
    assert "f.py:2:" in res.output


def test_grep_glob_filter(tmp_path: Path):
    ws = _ws(tmp_path)
    (ws / "a.py").write_text("hello\n", encoding="utf-8")
    (ws / "a.txt").write_text("hello\n", encoding="utf-8")
    res = asyncio.run(grep_tool("hello", None, glob="*.py", workspace=ws))
    assert res.ok
    assert "a.py" in res.output and "a.txt" not in res.output


def test_grep_multiline(tmp_path: Path):
    ws = _ws(tmp_path)
    (ws / "f.py").write_text("class Foo:\n    def bar(self):\n        pass\n", encoding="utf-8")
    res = asyncio.run(grep_tool(
        r"class \w+:\s+def \w+",
        None, output_mode="content", multiline=True, workspace=ws,
    ))
    assert res.ok
    assert "f.py" in res.output


# ── cache + invalidation ────────────────────────────────────


def test_cached_read_still_lets_edit_succeed(tmp_path: Path):
    """Regression: a cache-hit on `read` must not break the must-read-first
    invariant for `edit`. The read-stamp survives across cache hits because
    Edit relies on it; the cache only short-circuits the file IO, not the
    state mutation upstream of the call."""
    ws = _ws(tmp_path)
    f = ws / "f.py"
    f.write_text("answer = 1\n", encoding="utf-8")
    reg = ToolRegistry()
    reg.workspace_key = str(ws)

    async def reader(path: str):
        return await read_tool(path, workspace=ws, state=reg.state)

    async def editor(path: str, old_string: str, new_string: str):
        return await edit_tool(
            path, old_string, new_string,
            workspace=ws, state=reg.state, on_change=lambda p: reg.invalidate_path(str(p)),
        )

    reg.register(
        ToolSpec(name="read", description="r",
                 parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                 category="file"),
        reader,
    )
    reg.register(
        ToolSpec(name="edit", description="e",
                 parameters={"type": "object", "properties": {
                     "path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"},
                 }, "required": ["path", "old_string", "new_string"]},
                 category="file"),
        editor,
    )
    # Run 1: read populates both cache and reads-stamp; edit succeeds.
    r1 = asyncio.run(reg.call("read", {"path": "f.py"}))
    assert r1.ok
    e1 = asyncio.run(reg.call("edit", {"path": "f.py", "old_string": "answer = 1", "new_string": "answer = 42"}))
    assert e1.ok, e1.error
    # Edit just invalidated the cache for that path. Simulate "run 2": read again,
    # which gets a cold call (cache cleared) and re-stamps.
    r2 = asyncio.run(reg.call("read", {"path": "f.py"}))
    assert r2.ok
    # Now read once more — this hits cache.
    r3 = asyncio.run(reg.call("read", {"path": "f.py"}))
    assert r3.ok and r3.meta.get("cached") is True
    # The cache-hit path didn't re-run read_tool, but the prior cold read
    # already stamped state["reads"], so this edit must still succeed.
    e2 = asyncio.run(reg.call(
        "edit", {"path": "f.py", "old_string": "answer = 42", "new_string": "answer = 7"},
    ))
    assert e2.ok, e2.error
    assert "answer = 7" in f.read_text(encoding="utf-8")


def test_cache_returns_same_result_then_invalidates_on_write(tmp_path: Path):
    ws = _ws(tmp_path)
    f = ws / "f.py"
    f.write_text("v = 1\n", encoding="utf-8")
    reg = ToolRegistry()
    reg.workspace_key = str(ws)

    async def reader(path: str):
        return await read_tool(path, workspace=ws, state=reg.state)

    reg.register(
        ToolSpec(
            name="read",
            description="read",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            category="file",
        ),
        reader,
    )
    r1 = asyncio.run(reg.call("read", {"path": "f.py"}))
    assert r1.ok and not r1.meta.get("cached")
    r2 = asyncio.run(reg.call("read", {"path": "f.py"}))
    assert r2.ok and r2.meta.get("cached") is True
    # Mutate then invalidate — next call should miss cache
    f.write_text("v = 2\n", encoding="utf-8")
    reg.invalidate_path(str(f))
    r3 = asyncio.run(reg.call("read", {"path": "f.py"}))
    assert r3.ok and not r3.meta.get("cached")
    assert "v = 2" in r3.output


# ── todos ───────────────────────────────────────────────────


def test_todo_seed_and_status_transitions():
    todos = TodoList()
    todos.seed_from_plan_steps([
        {"n": 1, "description": "find files"},
        {"n": 2, "description": "edit them"},
    ])
    assert todos.progress() == {"pending": 2, "in_progress": 0, "completed": 0, "blocked": 0, "total": 2}
    todos.update_status(1, "in_progress")
    todos.update_status(1, "completed")
    todos.update_status(2, "blocked", note="missing pattern")
    p = todos.progress()
    assert p["completed"] == 1 and p["blocked"] == 1
    rendered = todos.render()
    assert "[x] 1." in rendered
    assert "[!] 2." in rendered
    assert "missing pattern" in rendered


def test_todo_add_appends_with_next_n():
    todos = TodoList()
    todos.seed_from_plan_steps([{"n": 1, "description": "first"}])
    new = todos.add("follow-up")
    assert new.n == 2
    assert todos.items[-1].content == "follow-up"


# ── project context ─────────────────────────────────────────


def test_discover_project_context_finds_localagent_md(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    sub = root / "src" / "deep"
    sub.mkdir(parents=True)
    (root / "LOCALAGENT.md").write_text("# Project rules\n- be concise\n", encoding="utf-8")
    found = discover_project_context(sub, ceiling=tmp_path)
    assert found is not None
    path, body = found
    assert path.name == "LOCALAGENT.md"
    assert "be concise" in body


def test_discover_project_context_priority(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "AGENTS.md").write_text("# agents", encoding="utf-8")
    (root / "LOCALAGENT.md").write_text("# localagent", encoding="utf-8")
    found = discover_project_context(root)
    assert found is not None
    path, _ = found
    assert path.name == "LOCALAGENT.md"  # higher priority


def test_discover_project_context_returns_none_when_missing(tmp_path: Path):
    root = tmp_path / "empty"
    root.mkdir()
    assert discover_project_context(root, ceiling=tmp_path) is None


# ── compact tool catalog ────────────────────────────────────


def test_compact_catalog_groups_by_category():
    reg = ToolRegistry()
    reg.register(
        ToolSpec(name="glob", description="g", parameters={"type": "object", "properties": {"p": {}}, "required": ["p"]}, category="search"),
        lambda **_: None,
    )
    reg.register(
        ToolSpec(name="read", description="r", parameters={"type": "object", "properties": {"path": {}}, "required": ["path"]}, category="file"),
        lambda **_: None,
    )
    cat = reg.compact_catalog()
    assert "## search" in cat and "## file" in cat
    assert "glob(p*) — g" in cat
    assert "read(path*) — r" in cat
