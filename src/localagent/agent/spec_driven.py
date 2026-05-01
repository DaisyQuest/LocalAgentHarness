"""SpecDrivenAgent — phased spec-driven development orchestrator.

The flow:

1. ``draft(goal)`` — turn a free-form user request into a structured Spec.
2. ``ask_questions(spec)`` — generate ranked, constrained clarifying
   questions. Caller collects answers from the user.
3. ``integrate_answers(spec, qa)`` — fold answers in, return updated spec.
4. ``assess_readiness(spec)`` — score 1–5; ``ready`` is true if score ≥ 4
   (caller may also force-ready via user override).
5. ``decompose(spec)`` — produce sequential ``WorkChunk``s with explicit
   ``acceptance`` criteria each.
6. ``execute(spec)`` — async-iterate: per chunk, run the planner-executor on
   a context-wrapped goal; verify acceptance against the live workspace;
   retry once on failure, otherwise mark blocked and continue.
7. ``verify(spec)`` — final pass over global acceptance criteria.

Every phase emits typed events so the CLI/web surface can render progress
without re-implementing the loop.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field

from ..core import ChatRequest, Message, Provider
from ..tools import ToolRegistry, ToolResult
from .meta_cognition import llm_json
from .planner_executor import PlannerExecutor
from .spec import (
    AcceptanceCriterion,
    ClarifyingQuestion,
    Spec,
    SpecReadiness,
    SpecVerification,
    WorkChunk,
)


# ── prompts ─────────────────────────────────────────────────


DRAFT_PROMPT = """You convert a user's feature request into a STRUCTURED SPEC.

Read the goal. Produce STRICT JSON with these fields:
{
  "title": "<5-7 word title>",
  "summary": "<one paragraph 'north star': what changes, for whom, why>",
  "requirements": ["<concrete observable behavior>", ...],
  "constraints": ["<technical or scope constraint>", ...],
  "out_of_scope": ["<things the user might mean but you'll explicitly NOT do>", ...]
}

Rules:
- 3-7 requirements. Each must be testable. Bad: "good UX". Good: "toggle persists across reload".
- 1-4 constraints. These are HARD limits (stack, perf, compat).
- 1-4 out_of_scope items if there's any ambiguity at all about scope; empty list if truly unambiguous.
- Do NOT invent features beyond the literal request.
- summary is one paragraph, ~2-4 sentences."""


QUESTIONS_PROMPT = """You are interrogating the user to lock down a spec. Produce ranked clarifying questions.

CONSTRAINTS — questions MUST be one of:
- "binary": yes/no question
- "choice": multiple choice; provide 2-5 distinct choices
- "value": ask for a specific concrete value (filename, number, identifier, copy text)

NEVER ask open-ended "what do you want / how should it work" — those are useless.

For each question, score importance 1-5:
- 5 = blocker: cannot start without this answer
- 4 = high: shapes architecture, painful to revisit
- 3 = medium: shapes implementation, recoverable
- 1-2 = nitpick: don't return these

Return at most 5 questions, only ones with importance >= 3, sorted by importance descending.

Examples (good):
- {"text": "Should the toggle persist across page reloads?", "kind": "binary", "importance": 5}
- {"text": "Where does the toggle live?", "kind": "choice", "choices": ["settings page", "header", "both"], "importance": 4}
- {"text": "What CSS variable name should encode the background color?", "kind": "value", "importance": 3}

Examples (bad — DO NOT produce):
- {"text": "How should this work?", ...}        # too vague
- {"text": "What's the user experience?", ...}  # not testable
- {"text": "Are there edge cases?", ...}        # not specific

If the spec is already clear enough that no important questions remain, return {"questions": []}.

Return STRICT JSON ONLY:
{"questions": [{"text": "...", "why": "<why this matters>", "kind": "binary|choice|value", "choices": [...], "importance": 1-5}]}"""


INTEGRATE_PROMPT = """You are folding the user's answers into the spec. Produce an UPDATED spec.

Rules:
- Modify summary/requirements/constraints/out_of_scope as the answers dictate.
- DO NOT invent new requirements that the answers didn't directly address.
- If an answer is "I don't know" or "you decide", PRESERVE the question (don't drop it) and note the agent's decision in the summary.
- Keep the title and goal unchanged.

Return STRICT JSON ONLY with the same shape as the input spec, but with updated content:
{"summary": "...", "requirements": [...], "constraints": [...], "out_of_scope": [...]}

Do NOT include any other fields."""


READINESS_PROMPT = """You judge whether a spec is ironclad enough to start work.

Score 1-5:
- 5 = ironclad. Every requirement is concrete and testable. No ambiguity worth asking about.
- 4 = strong. Minor judgment calls, but the agent can make them confidently.
- 3 = workable but vague. Requirements are loose; will likely produce wrong output.
- 1-2 = blocked. Critical info missing.

ready=true ONLY if score >= 4.

Return STRICT JSON ONLY:
{"score": 1-5, "ready": true|false, "blockers": ["<unresolved>", ...], "summary": "<one sentence>"}"""


DECOMPOSE_PROMPT = """You decompose a ready spec into bite-sized, sequentially-executable work chunks.

Each chunk:
- Title: 4-8 words
- Description: 1-2 sentences. Concrete — names files/symbols where possible.
- file_hints: list of likely file paths (best guess; planner will refine via grep/glob).
- acceptance: 1-4 explicit pass/fail conditions WITH a concrete verification method.

Acceptance verification format examples (good):
- {"text": "--color-bg is defined in :root", "verification": "grep -n '\\\\-\\\\-color-bg' src/index.css"}
- {"text": "ToggleButton component exists", "verification": "glob 'src/**/ToggleButton.{tsx,jsx}'"}
- {"text": "Settings page renders the toggle", "verification": "grep -nE 'ToggleButton|<ThemeToggle' src/Settings.tsx"}
- {"text": "tests pass", "verification": "shell_exec: pytest tests/test_theme.py -q"}

DO NOT produce vague verifications like "manual check", "looks right", "works correctly".

Also produce 2-5 GLOBAL acceptance criteria covering the spec as a whole.

Return STRICT JSON ONLY:
{
  "work_chunks": [
    {
      "title": "...", "description": "...",
      "file_hints": ["..."],
      "acceptance": [{"text": "...", "verification": "..."}]
    }
  ],
  "global_acceptance": [{"text": "...", "verification": "..."}]
}

Sequencing rule: order chunks so each can verify on its own without depending on a later chunk. Probe before mutate."""


VERIFY_PROMPT = """You are verifying whether an acceptance criterion is met. Decide met=true/false based on the evidence.

You will receive:
- The criterion text and its prescribed verification method
- The output of tool calls run to gather evidence

Be honest, not generous. If the evidence does not clearly demonstrate the criterion, met=false.

Return STRICT JSON ONLY:
{"met": true|false, "evidence": "<one or two sentences quoting/citing the tool output>"}"""


# ── config ──────────────────────────────────────────────────


class SpecDrivenConfig(BaseModel):
    max_rounds: int = 3                 # interrogation cap
    readiness_threshold: int = 4        # score >= this to auto-ready
    max_questions_per_round: int = 5
    chunk_retry_budget: int = 1         # retries per chunk after first failure
    reframe_per_chunk: bool = False     # spec already reframed; skip
    critique_per_chunk: bool = True
    done_check_per_chunk: bool = True
    history_chunks_in_context: int = 4


# ── orchestrator ────────────────────────────────────────────


class SpecDrivenAgent:
    def __init__(
        self,
        provider: Provider,
        tools: ToolRegistry,
        *,
        planner: PlannerExecutor,
        spec_model: str,
        verifier_model: str | None = None,
        config: SpecDrivenConfig | None = None,
    ):
        self.provider = provider
        self.tools = tools
        self.planner = planner
        # Heavy lifts (draft, decompose, integrate) run on the planner model.
        self.spec_model = spec_model
        # Structured yes/no judges (readiness, _verify_one) run on a smaller
        # model when one is available — same accuracy, ~10× faster.
        self.verifier_model = verifier_model or spec_model
        self.config = config or SpecDrivenConfig()

    # ── phase 1: draft ──────────────────────────────────────
    async def draft(self, goal: str, *, title_hint: str | None = None) -> Spec:
        data = await llm_json(
            self.provider, self.spec_model,
            system=DRAFT_PROMPT, user=f"User goal:\n{goal}",
            temperature=0.2,
        )
        title = title_hint or data.get("title", "Untitled spec")
        spec = Spec.new(title=title, goal=goal)
        spec.summary = str(data.get("summary", ""))
        spec.requirements = [str(x) for x in data.get("requirements", [])]
        spec.constraints = [str(x) for x in data.get("constraints", [])]
        spec.out_of_scope = [str(x) for x in data.get("out_of_scope", [])]
        spec.status = "draft"
        spec.log(f"draft: {len(spec.requirements)} requirements")
        return spec

    # ── phase 2: ask ────────────────────────────────────────
    async def ask_questions(self, spec: Spec) -> list[ClarifyingQuestion]:
        if spec.rounds >= self.config.max_rounds:
            return []
        user = (
            f"Current spec:\n{spec.model_dump_json(include={'goal', 'summary', 'requirements', 'constraints', 'out_of_scope'})}\n\n"
            f"This is interrogation round {spec.rounds + 1} of at most {self.config.max_rounds}."
        )
        try:
            data = await llm_json(
                self.provider, self.spec_model,
                system=QUESTIONS_PROMPT, user=user, temperature=0.2,
            )
        except Exception:
            return []
        out: list[ClarifyingQuestion] = []
        for i, q in enumerate(data.get("questions", []) or [], start=1):
            try:
                kind = q.get("kind", "value")
                if kind not in {"binary", "choice", "value"}:
                    kind = "value"
                imp = int(q.get("importance", 3))
                if imp < 3:
                    continue
                out.append(ClarifyingQuestion(
                    n=i,
                    text=str(q.get("text", "")).strip(),
                    why=str(q.get("why", "")).strip(),
                    importance=max(1, min(5, imp)),
                    kind=kind,  # type: ignore[arg-type]
                    choices=[str(c) for c in (q.get("choices") or [])][:5] if kind == "choice" else [],
                ))
            except Exception:
                continue
        out.sort(key=lambda q: q.importance, reverse=True)
        return out[: self.config.max_questions_per_round]

    # ── phase 3: integrate answers ──────────────────────────
    async def integrate_answers(
        self, spec: Spec, qa: list[tuple[ClarifyingQuestion, str]]
    ) -> Spec:
        if not qa:
            spec.rounds += 1
            spec.log("round noop (no answers)")
            return spec
        qa_lines = [f"Q{q.n} ({q.kind}, importance={q.importance}): {q.text}\nA: {a}" for q, a in qa]
        user = (
            f"Current spec:\n{spec.model_dump_json(include={'goal', 'summary', 'requirements', 'constraints', 'out_of_scope'})}\n\n"
            "User answers:\n" + "\n\n".join(qa_lines)
        )
        try:
            data = await llm_json(
                self.provider, self.spec_model,
                system=INTEGRATE_PROMPT, user=user, temperature=0.2,
            )
            if "summary" in data:
                spec.summary = str(data["summary"]).strip()
            if "requirements" in data:
                spec.requirements = [str(x) for x in data["requirements"]]
            if "constraints" in data:
                spec.constraints = [str(x) for x in data["constraints"]]
            if "out_of_scope" in data:
                spec.out_of_scope = [str(x) for x in data["out_of_scope"]]
        except Exception as e:
            spec.log(f"integrate failed: {e}")
        # Capture answered questions in history
        for q, a in qa:
            spec.log(f"Q{q.n}: {q.text} → {a}")
        spec.rounds += 1
        spec.touch()
        return spec

    # ── phase 4: readiness ──────────────────────────────────
    async def assess_readiness(self, spec: Spec) -> SpecReadiness:
        user = (
            f"Spec:\n{spec.model_dump_json(include={'goal','summary','requirements','constraints','out_of_scope'})}"
        )
        try:
            data = await llm_json(
                self.provider, self.verifier_model,
                system=READINESS_PROMPT, user=user, temperature=0.1,
            )
            score = max(1, min(5, int(data.get("score", 3))))
            r = SpecReadiness(
                score=score,
                ready=bool(data.get("ready", score >= self.config.readiness_threshold)),
                blockers=[str(b) for b in data.get("blockers", [])],
                summary=str(data.get("summary", "")),
            )
        except Exception as e:
            r = SpecReadiness(score=3, ready=False, blockers=[f"readiness check failed: {e}"], summary="")
        spec.readiness = r
        spec.touch()
        return r

    def force_ready(self, spec: Spec, *, reason: str = "user override") -> SpecReadiness:
        """Caller-driven escape hatch when the user says 'ship it'."""
        r = SpecReadiness(score=spec.readiness.score if spec.readiness else 4,
                          ready=True, blockers=[], summary=reason)
        spec.readiness = r
        spec.status = "ready"
        spec.log(f"force-ready: {reason}")
        return r

    # ── phase 5: decompose ──────────────────────────────────
    async def decompose(self, spec: Spec) -> Spec:
        user = (
            f"Ready spec:\n{spec.model_dump_json(include={'goal','summary','requirements','constraints','out_of_scope'})}"
        )
        try:
            data = await llm_json(
                self.provider, self.spec_model,
                system=DECOMPOSE_PROMPT, user=user, temperature=0.2,
            )
        except Exception as e:
            spec.log(f"decompose failed: {e}")
            raise
        chunks_raw = data.get("work_chunks", []) or []
        chunks: list[WorkChunk] = []
        for i, c in enumerate(chunks_raw, start=1):
            ac_list = []
            for j, ac in enumerate(c.get("acceptance", []) or [], start=1):
                ac_list.append(AcceptanceCriterion(
                    id=f"c{i}-ac{j}",
                    text=str(ac.get("text", "")).strip(),
                    verification=str(ac.get("verification", "")).strip(),
                ))
            chunks.append(WorkChunk(
                n=i,
                title=str(c.get("title", f"Chunk {i}")).strip(),
                description=str(c.get("description", "")).strip(),
                file_hints=[str(x) for x in (c.get("file_hints") or [])][:8],
                acceptance=ac_list,
            ))
        spec.work_chunks = chunks
        gac = []
        for j, ac in enumerate(data.get("global_acceptance", []) or [], start=1):
            gac.append(AcceptanceCriterion(
                id=f"global-ac{j}",
                text=str(ac.get("text", "")).strip(),
                verification=str(ac.get("verification", "")).strip(),
            ))
        spec.global_acceptance = gac
        spec.status = "ready"
        spec.log(f"decomposed into {len(chunks)} chunks, {len(gac)} global acceptance criteria")
        return spec

    # ── phase 6: execute ────────────────────────────────────
    async def execute(self, spec: Spec, *, on_save=None) -> AsyncIterator[dict[str, Any]]:
        """Iterate sequentially over chunks. For each: run planner-executor on
        a context-wrapped goal, verify acceptance against the workspace, retry
        once on failure, otherwise mark blocked and proceed.

        ``on_save`` (optional) is called after every chunk completion so the
        caller can persist progress.
        """
        if not spec.work_chunks:
            yield {"type": "spec_error", "error": "no work_chunks; call decompose() first"}
            return

        spec.status = "executing"
        yield {"type": "spec_status", "spec": _spec_summary(spec)}

        # Toggle the planner's per-chunk meta-cognition flags as configured.
        prev_flags = (self.planner.use_reframe, self.planner.use_critique, self.planner.use_done_check)
        self.planner.use_reframe = self.config.reframe_per_chunk
        self.planner.use_critique = self.config.critique_per_chunk
        self.planner.use_done_check = self.config.done_check_per_chunk

        try:
            for chunk in spec.work_chunks:
                if chunk.status in ("completed", "skipped"):
                    continue
                attempt = 0
                while True:
                    attempt += 1
                    chunk.status = "in_progress"
                    chunk.attempts = attempt
                    spec.touch()
                    yield {"type": "chunk_start", "chunk": chunk.model_dump(), "attempt": attempt}
                    if on_save:
                        on_save(spec)

                    # Wrap the chunk goal with spec context so the planner has
                    # the north-star + already-completed context, in <1KB.
                    preamble = spec.context_preamble(
                        max_chunks_in_history=self.config.history_chunks_in_context,
                    )
                    file_hints = ("\n\nFile hints (non-binding; verify with grep/glob):\n- "
                                  + "\n- ".join(chunk.file_hints)) if chunk.file_hints else ""
                    acc_lines = ("\n\nAcceptance for THIS chunk (verify before claiming done):\n- "
                                 + "\n- ".join(f"{a.text}  (check: {a.verification})" for a in chunk.acceptance))
                    retry_note = (
                        f"\n\nThis is RETRY {attempt}. Last error: {chunk.last_error[:300]}"
                        if attempt > 1 and chunk.last_error else ""
                    )
                    chunk_goal = (
                        f"<spec_context>\n{preamble}\n</spec_context>\n\n"
                        f"Task (chunk {chunk.n} of {len(spec.work_chunks)}): {chunk.description}"
                        f"{file_hints}{acc_lines}{retry_note}"
                    )

                    # Stream all planner-executor events under a chunk envelope so
                    # the surface can render them grouped per chunk.
                    final_text = ""
                    async for ev in self.planner.run(chunk_goal):
                        ev["chunk_n"] = chunk.n
                        if ev.get("type") == "done":
                            final_text = ev.get("answer", "")
                        yield ev

                    # ── verify acceptance against the live workspace ───
                    verification_results: list[AcceptanceCriterion] = []
                    for ac in chunk.acceptance:
                        verified = await self._verify_one(ac)
                        verification_results.append(verified)
                        yield {
                            "type": "criterion_verified",
                            "chunk_n": chunk.n,
                            "criterion": verified.model_dump(),
                        }
                    chunk.acceptance = verification_results
                    all_met = all(ac.met for ac in chunk.acceptance) if chunk.acceptance else True

                    if all_met:
                        chunk.status = "completed"
                        chunk.notes = (final_text.splitlines()[0][:200] if final_text else "completed")
                        spec.touch()
                        yield {"type": "chunk_done", "chunk": chunk.model_dump()}
                        if on_save:
                            on_save(spec)
                        break

                    # Failure path — retry or block
                    failed = [ac for ac in chunk.acceptance if not ac.met]
                    chunk.last_error = "; ".join(f"{ac.text} (no evidence)" for ac in failed)[:400]
                    if attempt <= self.config.chunk_retry_budget:
                        yield {"type": "chunk_retry", "chunk_n": chunk.n,
                               "failed": [ac.model_dump() for ac in failed]}
                        continue
                    chunk.status = "blocked"
                    spec.touch()
                    yield {"type": "chunk_blocked", "chunk": chunk.model_dump()}
                    if on_save:
                        on_save(spec)
                    break

            # ── final spec verification ───────────────────────
            final = await self.verify(spec)
            yield {"type": "spec_verified", "verification": final.model_dump(),
                   "spec": _spec_summary(spec)}
        finally:
            self.planner.use_reframe, self.planner.use_critique, self.planner.use_done_check = prev_flags

    # ── phase 7: final verify ───────────────────────────────
    async def verify(self, spec: Spec) -> SpecVerification:
        verified_global: list[AcceptanceCriterion] = []
        for ac in spec.global_acceptance:
            verified_global.append(await self._verify_one(ac))
        spec.global_acceptance = verified_global
        chunk_done = sum(1 for c in spec.work_chunks if c.status == "completed")
        chunk_total = len(spec.work_chunks)
        # Count criteria across both chunks and global
        all_acs: list[AcceptanceCriterion] = list(verified_global)
        for c in spec.work_chunks:
            all_acs.extend(c.acceptance)
        met = sum(1 for ac in all_acs if ac.met)
        total = len(all_acs)
        gaps = [
            f"chunk {c.n} ({c.title}) blocked: {c.last_error or 'see verification'}"
            for c in spec.work_chunks if c.status == "blocked"
        ]
        gaps.extend(f"global criterion not met: {ac.text}" for ac in verified_global if ac.met is False)
        if chunk_done == chunk_total and met == total:
            overall: str = "verified"
        elif chunk_done > 0 or met > 0:
            overall = "partial"
        else:
            overall = "failed"
        v = SpecVerification(
            overall=overall,  # type: ignore[arg-type]
            chunks_completed=chunk_done,
            chunks_total=chunk_total,
            criteria_met=met,
            criteria_total=total,
            gaps=gaps,
        )
        spec.verification = v
        # Map verification verdict to spec status. "partial" is its own state
        # so `spec list` doesn't lie about whether execution is still running.
        spec.status = {
            "verified": "verified",
            "partial": "partial",
            "failed": "failed",
        }.get(overall, "failed")
        spec.touch()
        return v

    # ── helpers ─────────────────────────────────────────────
    async def _verify_one(self, ac: AcceptanceCriterion) -> AcceptanceCriterion:
        """Verify one acceptance criterion by running its prescribed verification.

        We parse the verification string heuristically into a concrete tool call
        (grep/glob/read/shell_exec). If that yields evidence, an LLM judge
        decides met/not-met. If parsing fails, we fall back to the LLM judge
        with the raw verification text and no evidence (which usually returns
        not-met — the right default for "couldn't actually check").
        """
        evidence_parts: list[str] = []
        tool_called = ""
        v = ac.verification.strip()
        try:
            tool, args = _parse_verification(v)
        except ValueError:
            tool, args = "", {}
        if tool:
            try:
                result: ToolResult = await self.tools.call(tool, args)
                tool_called = tool
                snippet = (result.output or result.error or "")[:1500]
                evidence_parts.append(f"$ {tool} {json.dumps(args)}\nok={result.ok}\n{snippet}")
            except Exception as e:
                evidence_parts.append(f"tool {tool} raised: {e}")

        evidence_blob = "\n\n".join(evidence_parts) or "(no evidence — verification couldn't be parsed into a tool call)"
        # LLM judges met/not-met (structured yes/no — small model is enough)
        try:
            data = await llm_json(
                self.provider, self.verifier_model,
                system=VERIFY_PROMPT,
                user=f"Criterion: {ac.text}\nVerification method: {ac.verification}\n\nEvidence:\n{evidence_blob}",
                temperature=0.1,
            )
            met = bool(data.get("met", False))
            evidence_text = str(data.get("evidence", ""))[:600]
        except Exception as e:
            met, evidence_text = False, f"verifier crashed: {e}"
        return AcceptanceCriterion(
            id=ac.id, text=ac.text, verification=ac.verification,
            met=met, evidence=evidence_text + (f"\n[via {tool_called}]" if tool_called else ""),
        )


# ── verification parser ─────────────────────────────────────


_VERB_TO_TOOL = {
    "grep": "grep",
    "rg": "grep",
    "glob": "glob",
    "ls": "glob",
    "find": "glob",
    "read": "read",
    "cat": "read",
    "shell_exec": "shell_exec",
    "shell": "shell_exec",
    "$": "shell_exec",
    "run": "shell_exec",
}


def _parse_verification(text: str) -> tuple[str, dict[str, Any]]:
    """Best-effort parser. Recognized forms:

    * ``grep <pattern> [path]``  / ``grep -n <pattern> <path>``
    * ``glob <pattern>``         / ``ls <pattern>``
    * ``read <path>``            / ``cat <path>``
    * ``shell_exec: <cmd>``      / ``$ <cmd>``  / ``run: <cmd>``

    Returns (tool, args). Raises ValueError if no recognized form.
    """
    s = text.strip()
    if not s:
        raise ValueError("empty verification")
    # explicit "tool: rest"
    if ":" in s:
        head, rest = s.split(":", 1)
        head = head.strip().lower()
        if head in _VERB_TO_TOOL and rest.strip():
            tool = _VERB_TO_TOOL[head]
            return _build_args(tool, rest.strip())
    # leading verb form
    parts = s.split(None, 1)
    if not parts:
        raise ValueError("no verb")
    verb = parts[0].lower().lstrip("`").rstrip(":")
    if verb in _VERB_TO_TOOL:
        rest = parts[1] if len(parts) > 1 else ""
        return _build_args(_VERB_TO_TOOL[verb], rest)
    raise ValueError(f"unrecognized verification verb: {verb!r}")


def _build_args(tool: str, rest: str) -> tuple[str, dict[str, Any]]:
    rest = rest.strip().strip("`")
    if tool == "grep":
        # strip common flags; keep -i meaningful
        ci = False
        toks = rest.split()
        cleaned: list[str] = []
        i = 0
        while i < len(toks):
            t = toks[i]
            if t in {"-n", "-E", "-r", "-R", "-H", "-l"}:
                i += 1
                continue
            if t == "-i":
                ci = True
                i += 1
                continue
            if t.startswith("-"):
                i += 1
                continue
            cleaned.append(t)
            i += 1
        if not cleaned:
            raise ValueError("grep: no pattern")
        # First non-flag is pattern (may be quoted), rest is path
        pattern = cleaned[0].strip("'\"")
        path = " ".join(cleaned[1:]).strip("'\"") or None
        args: dict[str, Any] = {"pattern": pattern, "output_mode": "content"}
        if path:
            args["path"] = path
        if ci:
            args["case_insensitive"] = True
        return "grep", args
    if tool == "glob":
        pattern = rest.strip().strip("'\"")
        if not pattern:
            raise ValueError("glob: no pattern")
        return "glob", {"pattern": pattern}
    if tool == "read":
        path = rest.strip().strip("'\"")
        if not path:
            raise ValueError("read: no path")
        return "read", {"path": path}
    if tool == "shell_exec":
        cmd = rest.strip()
        if not cmd:
            raise ValueError("shell_exec: no command")
        return "shell_exec", {"command": cmd}
    raise ValueError(f"unknown tool: {tool}")


def _spec_summary(spec: Spec) -> dict[str, Any]:
    return {
        "id": spec.id, "title": spec.title, "status": spec.status,
        "rounds": spec.rounds, "progress": spec.progress(),
    }
