"""Tests for spec-driven development.

The deterministic surfaces — model invariants, persistence/resumability,
verification parsing, context preamble budget — are tested directly. The
LLM-driven phases are tested with a stub provider so we catch wiring
regressions without spinning up Ollama.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from localagent.agent.spec import (
    AcceptanceCriterion,
    ClarifyingQuestion,
    Spec,
    WorkChunk,
)
from localagent.agent.spec_driven import (
    SpecDrivenAgent,
    SpecDrivenConfig,
    _build_args,
    _parse_verification,
)
from localagent.agent.spec_store import SpecStore
from localagent.core import ChatRequest, ChatResponse, Message, Provider, Usage
from localagent.tools import build_default_registry
from localagent.config import ToolPolicy


# ── stub provider ───────────────────────────────────────────


class StubProvider(Provider):
    """A scripted provider — pops a JSON-or-text reply per call.

    Use ``script(payload)`` to add the next response. ``provider.calls``
    captures the request payload for assertions.
    """

    name = "stub"

    def __init__(self):
        self.queue: list[Any] = []
        self.calls: list[dict[str, Any]] = []

    def script(self, payload: Any) -> "StubProvider":
        self.queue.append(payload)
        return self

    async def chat(self, req: ChatRequest) -> ChatResponse:
        self.calls.append({"system": req.messages[0].content, "user": req.messages[-1].content})
        if not self.queue:
            raise RuntimeError("StubProvider: queue empty")
        payload = self.queue.pop(0)
        content = json.dumps(payload) if not isinstance(payload, str) else payload
        return ChatResponse(message=Message(role="assistant", content=content), usage=Usage())

    def stream(self, req: ChatRequest):  # type: ignore[override]
        async def _aiter() -> AsyncIterator:
            yield None
        return _aiter()

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        return [[0.0] * 8 for _ in inputs]


# ── model invariants ────────────────────────────────────────


def test_spec_new_assigns_id_and_status():
    s = Spec.new(title="Dark Mode", goal="Add a dark mode toggle")
    assert s.id.startswith("dark-mode-")
    assert s.status == "draft"
    assert s.goal == "Add a dark mode toggle"


def test_spec_progress_counts():
    s = Spec.new(title="x", goal="x")
    s.work_chunks = [
        WorkChunk(n=1, title="a", description="", status="completed"),
        WorkChunk(n=2, title="b", description="", status="in_progress"),
        WorkChunk(n=3, title="c", description="", status="blocked"),
    ]
    p = s.progress()
    assert p["completed"] == 1 and p["in_progress"] == 1 and p["blocked"] == 1 and p["total"] == 3


def test_context_preamble_caps_history(tmp_path):
    s = Spec.new(title="Big", goal="g")
    s.summary = "north star"
    s.requirements = ["r1", "r2"]
    s.constraints = ["c1"]
    # 10 completed chunks; only last 4 should appear, rest collapsed.
    s.work_chunks = [
        WorkChunk(n=i, title=f"chunk{i}", description="", status="completed",
                  notes=f"did chunk {i}")
        for i in range(1, 11)
    ]
    pre = s.context_preamble(max_chunks_in_history=4)
    # Last 4 by name (use trailing dash to disambiguate chunk1 from chunk10)
    for i in (7, 8, 9, 10):
        assert f"chunk{i} " in pre or f"chunk{i}\n" in pre
    # Earlier ones collapsed
    assert "+6 earlier chunks completed" in pre
    # chunks 1..6 should be collapsed away — chunk2..chunk6 are unambiguous
    for i in (2, 3, 4, 5, 6):
        assert f"chunk{i} " not in pre and f"chunk{i}\n" not in pre


def test_context_preamble_budget(tmp_path):
    """Budget the advisor flagged: must stay under ~1KB on a normal spec."""
    s = Spec.new(title="Reasonable", goal="g")
    s.summary = "A reasonable summary " * 5
    s.requirements = [f"requirement {i}" for i in range(5)]
    s.constraints = [f"constraint {i}" for i in range(2)]
    s.out_of_scope = ["one out of scope item"]
    s.work_chunks = [
        WorkChunk(n=i, title=f"chunk{i}", description="", status="completed",
                  notes=f"did chunk {i}")
        for i in range(1, 5)
    ]
    pre = s.context_preamble()
    assert len(pre) < 1500  # comfortably under 1KB target with prose


# ── persistence + resumability ──────────────────────────────


def test_spec_store_roundtrip(tmp_path: Path):
    store = SpecStore(tmp_path / "specs")
    s = Spec.new(title="X", goal="do x")
    s.requirements = ["a", "b"]
    s.work_chunks = [WorkChunk(n=1, title="t", description="d",
                                acceptance=[AcceptanceCriterion(id="c1-ac1", text="ok",
                                                                verification="grep x src/")])]
    store.save(s)
    loaded = store.load(s.id)
    assert loaded is not None
    assert loaded.title == "X"
    assert loaded.requirements == ["a", "b"]
    assert loaded.work_chunks[0].acceptance[0].verification == "grep x src/"


def test_spec_store_resumability(tmp_path: Path):
    """Save mid-spec, reconstruct from disk, mutate, save again. Round-trip
    survives status changes and partial chunk completion (the safety net)."""
    store = SpecStore(tmp_path / "specs")
    s = Spec.new(title="Resume me", goal="g")
    s.work_chunks = [
        WorkChunk(n=1, title="a", description="", status="completed", notes="done first"),
        WorkChunk(n=2, title="b", description="", status="in_progress"),
        WorkChunk(n=3, title="c", description="", status="pending"),
    ]
    s.status = "executing"
    store.save(s)

    # Simulate Ctrl-C: drop everything, reload
    fresh = store.load(s.id)
    assert fresh is not None
    assert fresh.status == "executing"
    assert fresh.work_chunks[0].status == "completed"
    assert fresh.work_chunks[1].status == "in_progress"
    assert fresh.work_chunks[2].status == "pending"

    # Continue from where we left off
    fresh.work_chunks[1].status = "completed"
    fresh.work_chunks[2].status = "completed"
    fresh.status = "verified"
    store.save(fresh)

    final = store.load(s.id)
    assert final.status == "verified"
    assert all(c.status == "completed" for c in final.work_chunks)


def test_spec_store_list_sorted_by_updated(tmp_path: Path):
    store = SpecStore(tmp_path / "specs")
    a = Spec.new(title="A", goal="g")
    b = Spec.new(title="B", goal="g")
    store.save(a)
    time.sleep(0.01)  # ensure distinct mtimes
    store.save(b)
    rows = store.list()
    assert len(rows) == 2
    assert rows[0]["id"] == b.id  # newer first


# ── verification parser ─────────────────────────────────────


@pytest.mark.parametrize("text,expected_tool,expected_keys", [
    ("grep -n 'foo' src/", "grep", {"pattern", "path", "output_mode"}),
    ("rg foo src/main.py", "grep", {"pattern", "path", "output_mode"}),
    ("grep -i 'Foo' src/", "grep", {"pattern", "path", "case_insensitive"}),
    ("glob '**/*.py'", "glob", {"pattern"}),
    ("ls **/*.tsx", "glob", {"pattern"}),
    ("read src/foo.py", "read", {"path"}),
    ("cat package.json", "read", {"path"}),
    ("shell_exec: pytest -q", "shell_exec", {"command"}),
    ("$ npm test", "shell_exec", {"command"}),
    ("run: make build", "shell_exec", {"command"}),
])
def test_parse_verification(text, expected_tool, expected_keys):
    tool, args = _parse_verification(text)
    assert tool == expected_tool
    assert expected_keys.issubset(args.keys())


def test_parse_verification_rejects_garbage():
    with pytest.raises(ValueError):
        _parse_verification("manual check that it works")
    with pytest.raises(ValueError):
        _parse_verification("")


def test_parse_verification_grep_no_path():
    tool, args = _parse_verification("grep myFunction")
    assert tool == "grep"
    assert args["pattern"] == "myFunction"
    assert "path" not in args


# ── orchestrator phases (mocked LLM) ────────────────────────


@pytest.mark.asyncio
async def test_draft_phase_populates_spec():
    provider = StubProvider().script({
        "title": "Dark Mode Toggle",
        "summary": "A toggle in settings flips light/dark.",
        "requirements": ["toggle visible in settings", "persists across reload"],
        "constraints": ["use CSS variables"],
        "out_of_scope": ["system-preference detection"],
    })
    reg = build_default_registry(ToolPolicy())

    # Build a minimal planner-executor stub (won't be invoked here)
    from localagent.agent.planner_executor import PlannerExecutor
    pe = PlannerExecutor(provider, reg, planner_model="m", executor_model="m")
    sda = SpecDrivenAgent(provider, reg, planner=pe, spec_model="m")
    spec = await sda.draft("Add a dark mode toggle", title_hint=None)
    assert spec.title == "Dark Mode Toggle"
    assert "settings" in spec.summary.lower()
    assert len(spec.requirements) == 2
    assert spec.status == "draft"


@pytest.mark.asyncio
async def test_questions_filter_low_importance_and_open_ended():
    provider = StubProvider().script({
        "questions": [
            {"text": "Where does the toggle live?", "kind": "choice",
             "choices": ["settings", "header"], "importance": 5, "why": "shapes UI"},
            {"text": "Should it persist?", "kind": "binary", "importance": 4},
            {"text": "Nice color?", "kind": "value", "importance": 2},  # below threshold
        ]
    })
    reg = build_default_registry(ToolPolicy())
    from localagent.agent.planner_executor import PlannerExecutor
    pe = PlannerExecutor(provider, reg, planner_model="m", executor_model="m")
    sda = SpecDrivenAgent(provider, reg, planner=pe, spec_model="m")
    spec = Spec.new(title="x", goal="x")
    qs = await sda.ask_questions(spec)
    assert len(qs) == 2  # the importance=2 was filtered
    assert qs[0].importance == 5  # sorted desc
    assert qs[0].kind == "choice"


@pytest.mark.asyncio
async def test_questions_capped_at_max_rounds():
    provider = StubProvider()
    reg = build_default_registry(ToolPolicy())
    from localagent.agent.planner_executor import PlannerExecutor
    pe = PlannerExecutor(provider, reg, planner_model="m", executor_model="m")
    sda = SpecDrivenAgent(provider, reg, planner=pe, spec_model="m",
                          config=SpecDrivenConfig(max_rounds=2))
    spec = Spec.new(title="x", goal="x")
    spec.rounds = 2  # already at cap
    qs = await sda.ask_questions(spec)
    assert qs == []
    assert provider.calls == []  # didn't even call LLM


@pytest.mark.asyncio
async def test_force_ready_marks_spec_ready():
    provider = StubProvider()
    reg = build_default_registry(ToolPolicy())
    from localagent.agent.planner_executor import PlannerExecutor
    pe = PlannerExecutor(provider, reg, planner_model="m", executor_model="m")
    sda = SpecDrivenAgent(provider, reg, planner=pe, spec_model="m")
    spec = Spec.new(title="x", goal="x")
    r = sda.force_ready(spec, reason="just ship")
    assert r.ready is True
    assert spec.status == "ready"
    assert any("force-ready" in line for line in spec.history)


@pytest.mark.asyncio
async def test_decompose_builds_chunks_with_acceptance():
    provider = StubProvider().script({
        "work_chunks": [
            {"title": "Add CSS scaffold", "description": "Define theme variables in :root.",
             "file_hints": ["src/index.css"],
             "acceptance": [
                 {"text": "--color-bg defined", "verification": "grep -- '--color-bg' src/index.css"},
             ]},
            {"title": "Wire toggle", "description": "Build the toggle component.",
             "file_hints": ["src/Settings.tsx"],
             "acceptance": [
                 {"text": "ToggleButton renders", "verification": "glob src/**/ToggleButton.tsx"},
             ]},
        ],
        "global_acceptance": [
            {"text": "no console errors", "verification": "shell_exec: npm test"},
        ],
    })
    reg = build_default_registry(ToolPolicy())
    from localagent.agent.planner_executor import PlannerExecutor
    pe = PlannerExecutor(provider, reg, planner_model="m", executor_model="m")
    sda = SpecDrivenAgent(provider, reg, planner=pe, spec_model="m")
    spec = Spec.new(title="x", goal="x")
    spec = await sda.decompose(spec)
    assert len(spec.work_chunks) == 2
    assert spec.work_chunks[0].n == 1
    assert spec.work_chunks[1].acceptance[0].verification.startswith("glob")
    assert len(spec.global_acceptance) == 1
    assert spec.status == "ready"


@pytest.mark.asyncio
async def test_verify_one_real_grep_then_judge(tmp_path: Path):
    """The verifier MUST call a real tool (not just consult done_check claims)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.css").write_text(":root {\n  --color-bg: #fff;\n}\n", encoding="utf-8")

    # Stub the LLM judge: it should look at the evidence and say met=true
    provider = StubProvider().script({"met": True, "evidence": "found --color-bg in f.css"})
    reg = build_default_registry(ToolPolicy(workspace=ws))
    from localagent.agent.planner_executor import PlannerExecutor
    pe = PlannerExecutor(provider, reg, planner_model="m", executor_model="m")
    sda = SpecDrivenAgent(provider, reg, planner=pe, spec_model="m")

    ac = AcceptanceCriterion(id="ac1", text="--color-bg defined",
                             verification="grep -- '--color-bg' f.css")
    verified = await sda._verify_one(ac)
    assert verified.met is True
    # The judge call must have included real evidence (not just the criterion text)
    last_call = provider.calls[-1]
    assert "color-bg" in last_call["user"]
    # And we annotated which tool was used
    assert "grep" in verified.evidence


@pytest.mark.asyncio
async def test_verify_one_no_evidence_when_unparseable():
    """If verification can't be parsed into a tool call, evidence is absent and
    the judge typically returns met=false (we stub it that way for determinism)."""
    provider = StubProvider().script({"met": False, "evidence": "no tool call ran"})
    reg = build_default_registry(ToolPolicy())
    from localagent.agent.planner_executor import PlannerExecutor
    pe = PlannerExecutor(provider, reg, planner_model="m", executor_model="m")
    sda = SpecDrivenAgent(provider, reg, planner=pe, spec_model="m")

    ac = AcceptanceCriterion(id="ac1", text="works correctly",
                             verification="manual inspection")
    verified = await sda._verify_one(ac)
    assert verified.met is False
    last_call = provider.calls[-1]
    assert "couldn't be parsed" in last_call["user"]


# ── _build_args edge cases ──────────────────────────────────


def test_build_args_grep_strips_flags():
    tool, args = _build_args("grep", "-n -E -i 'foo' src/main.py")
    assert tool == "grep"
    assert args["pattern"] == "foo"
    assert args["path"] == "src/main.py"
    assert args["case_insensitive"] is True


def test_build_args_glob_unwraps_quotes():
    tool, args = _build_args("glob", "'**/*.py'")
    assert tool == "glob"
    assert args["pattern"] == "**/*.py"


# ── end-to-end loop ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_loop_draft_to_verified(tmp_path: Path):
    """Walk the spec through every phase with a scripted provider.

    Catches wiring bugs (wrong field name, dropped state mutation, status
    transition) that hide between phase-isolation unit tests.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "theme.css").write_text(":root {\n  --color-bg: white;\n}\n", encoding="utf-8")

    # Script the provider with a payload for every LLM call, in order.
    provider = StubProvider()
    # 1. draft
    provider.script({
        "title": "Theme",
        "summary": "Add color tokens.",
        "requirements": ["--color-bg defined"],
        "constraints": [],
        "out_of_scope": [],
    })
    # 2. ask_questions: returns one question
    provider.script({
        "questions": [
            {"text": "Use HSL or hex?", "kind": "choice",
             "choices": ["hsl", "hex"], "importance": 4, "why": "stylistic"},
        ],
    })
    # 3. integrate_answers
    provider.script({
        "summary": "Add color tokens using hex.",
        "requirements": ["--color-bg defined"],
        "constraints": ["use hex format"],
        "out_of_scope": [],
    })
    # 4. assess_readiness
    provider.script({"score": 5, "ready": True, "blockers": [], "summary": "ironclad"})
    # 5. decompose
    provider.script({
        "work_chunks": [
            {"title": "Define bg token", "description": "Add --color-bg in :root.",
             "file_hints": ["theme.css"],
             "acceptance": [
                 {"text": "--color-bg defined", "verification": "grep -- '--color-bg' theme.css"},
             ]},
        ],
        "global_acceptance": [
            {"text": "color tokens present", "verification": "grep '--color-' theme.css"},
        ],
    })
    # The execute phase invokes the planner (reframe/critique/done_check skipped
    # by config) and then verifier. We need: 1 plan call (planner json), per-step
    # synthesizer stream is skipped because we don't reach token streaming with
    # the stubbed planner. Actually the PlannerExecutor.run() calls plan() which
    # uses llm_json — that's one call. Then no executor LLM calls if step has
    # tool=null and we use a tool, but here we plan a single step that just
    # describes the work. The synthesizer streams.
    #
    # To keep this test deterministic and fast, let's preempt by configuring
    # SpecDrivenConfig to skip critique/done_check, then provide ONLY:
    #   - 1 planner JSON (the plan itself)
    #   - 1 synthesizer-streamed response
    #   - 2 verifier JSONs (per-criterion + global)
    #
    # The PlannerExecutor's synthesizer uses provider.stream() which our stub
    # short-circuits; provider.chat() handles plan + verify.
    provider.script({  # planner output (Plan JSON)
        "goal": "Define bg token",
        "steps": [{"n": 1, "description": "verify token exists", "tool": "read",
                   "arguments": {"path": "theme.css"}}],
    })
    # Verifier judges: per-chunk criterion + global criterion
    provider.script({"met": True, "evidence": "found in theme.css"})
    provider.script({"met": True, "evidence": "found --color- prefix"})

    reg = build_default_registry(ToolPolicy(workspace=ws))
    from localagent.agent.planner_executor import PlannerExecutor
    pe = PlannerExecutor(provider, reg, planner_model="m", executor_model="m")
    sda = SpecDrivenAgent(
        provider, reg, planner=pe, spec_model="m", verifier_model="m",
        config=SpecDrivenConfig(
            max_rounds=1, reframe_per_chunk=False,
            critique_per_chunk=False, done_check_per_chunk=False,
        ),
    )

    # Phase 1
    spec = await sda.draft("Add color tokens", title_hint=None)
    assert spec.status == "draft"

    # Phase 2: questions
    qs = await sda.ask_questions(spec)
    assert len(qs) == 1
    spec.open_questions = qs

    # Phase 3: integrate user answer
    spec = await sda.integrate_answers(spec, [(qs[0], "hex")])
    assert any("hex" in c for c in spec.constraints)
    assert spec.rounds == 1

    # Phase 4: readiness
    r = await sda.assess_readiness(spec)
    assert r.ready is True

    # Phase 5: decompose
    spec.status = "ready"
    spec = await sda.decompose(spec)
    assert len(spec.work_chunks) == 1

    # Phase 6: execute (and final verify chained inside)
    events: list[dict] = []
    async for ev in sda.execute(spec):
        events.append(ev)

    types = [e["type"] for e in events]
    assert "chunk_start" in types
    assert "chunk_done" in types
    assert "criterion_verified" in types
    assert "spec_verified" in types

    # Final state
    assert spec.status == "verified"
    assert spec.verification.overall == "verified"
    assert spec.verification.chunks_completed == 1
    assert spec.work_chunks[0].status == "completed"
    # All chunk acceptance criteria came back met=True
    assert all(ac.met for ac in spec.work_chunks[0].acceptance)
    assert all(ac.met for ac in spec.global_acceptance)


@pytest.mark.asyncio
async def test_partial_status_when_chunk_blocked(tmp_path: Path):
    """A chunk that fails verification after retries → blocked → spec status partial."""
    ws = tmp_path / "ws"
    ws.mkdir()

    provider = StubProvider()
    # Decompose stub
    provider.script({
        "work_chunks": [
            {"title": "Failing chunk", "description": "Do impossible thing.",
             "acceptance": [{"text": "impossible", "verification": "manual check"}]},
        ],
        "global_acceptance": [],
    })
    # Plan call (1st attempt)
    provider.script({"goal": "x", "steps": [{"n": 1, "description": "no-op", "tool": None, "arguments": {}}]})
    # Verifier says not met (1st attempt)
    provider.script({"met": False, "evidence": "no tool ran"})
    # Plan call (retry attempt)
    provider.script({"goal": "x", "steps": [{"n": 1, "description": "no-op", "tool": None, "arguments": {}}]})
    # Verifier says not met (retry)
    provider.script({"met": False, "evidence": "still no tool"})
    # Final verify pass over global_acceptance — none, so no calls

    reg = build_default_registry(ToolPolicy(workspace=ws))
    from localagent.agent.planner_executor import PlannerExecutor
    pe = PlannerExecutor(provider, reg, planner_model="m", executor_model="m")
    sda = SpecDrivenAgent(
        provider, reg, planner=pe, spec_model="m", verifier_model="m",
        config=SpecDrivenConfig(
            chunk_retry_budget=1,
            reframe_per_chunk=False, critique_per_chunk=False, done_check_per_chunk=False,
        ),
    )

    spec = Spec.new(title="t", goal="g")
    spec.status = "ready"
    spec = await sda.decompose(spec)

    events: list[dict] = []
    async for ev in sda.execute(spec):
        events.append(ev)

    types = [e["type"] for e in events]
    assert "chunk_retry" in types  # retried once
    assert "chunk_blocked" in types  # then blocked
    assert spec.work_chunks[0].status == "blocked"
    assert spec.status in ("partial", "failed")
    assert spec.verification.overall in ("partial", "failed")
