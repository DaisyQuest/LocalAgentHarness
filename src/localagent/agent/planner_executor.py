from __future__ import annotations

import json
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field

from ..core import ChatRequest, Message, Provider
from ..tools import ToolRegistry, ToolResult
from .meta_cognition import critique, done_check, llm_json, reframe
from .todos import TodoList


class PlanStep(BaseModel):
    n: int
    description: str
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    goal: str
    steps: list[PlanStep]


class StepResult(BaseModel):
    step: PlanStep
    tool_result: ToolResult | None = None
    notes: str = ""


PLANNER_PROMPT_TMPL = """You are the PLANNER. Decompose the user's goal into 1–{max_steps} concrete steps.

Tool catalog (compact). Each line is `name(args*) — description`; * marks required.
Types: ``str|int|num|boo``; arrays of objects show their inner keys, e.g. ``[{old_string, new_string}]``.
If a tool's argument shape is still unclear, add an early step that calls
``tool_search`` with ``select:<name>`` to fetch the full JSON schema before the step that uses it.

{tool_list}

Output STRICT JSON only, no prose:
{{
  "goal": "<restated goal>",
  "steps": [
    {{"n": 1, "description": "...", "tool": "<tool name or null>", "arguments": {{...}}}}
  ]
}}

Discipline:
- Each step is small and verifiable.
- For code work, prefer this order: glob/grep to find relevant files → read to inspect → edit (precise) over file_write (full overwrite) → re-read or grep to verify.
- If a later step depends on a fact (file exists, symbol present, URL valid), include an EARLIER probe step that verifies it.
- Use the cheapest tool that fits. read beats shell_exec for reading. grep beats reading every file.
- Do not include steps that "explain" or "summarize" — the synthesizer handles that.
- Do not exceed scope. If asked to fix one bug, do not refactor unrelated code.
- Before `edit`, the SAME file must have been `read` in an earlier step (the edit tool enforces this)."""

REVISION_PROMPT_TMPL = """The reviewer flagged issues with your plan. Revise it to address them.

Reviewer issues:
{issues}

Reviewer summary: {summary}

Your previous plan:
{prev_plan}

Output the revised plan in the SAME strict JSON format. Keep what worked; address every HIGH severity issue."""

EXECUTOR_PROMPT = """You are the EXECUTOR. Carry out one step.
If the step has a tool, do NOT call it (the runner does); produce a brief reasoning note.
If no tool, produce the requested content directly. Be concise."""

SYNTHESIZER_PROMPT_BASE = """You are the SYNTHESIZER. Given goal, plan, and step results, write the final answer. Be clear and direct."""

SYNTHESIZER_PROMPT_WITH_DONE = """You are the SYNTHESIZER. Given goal, plan, step results, and a done-check report, write the final answer.

The done-check is authoritative. If it says "partial" or "failed":
- Tell the user honestly what was and wasn't accomplished
- List the gaps explicitly
- Do NOT claim success the report doesn't support
If "complete", deliver the answer cleanly without dwelling on the verification."""


class PlannerExecutor:
    def __init__(
        self, provider: Provider, tools: ToolRegistry, *,
        planner_model: str, executor_model: str, max_steps: int = 8,
        compose_strategy: "callable | None" = None,
        # Meta-cognition
        use_reframe: bool = True,
        use_critique: bool = True,
        use_done_check: bool = True,
        json_retries: int = 2,
        ambiguity_threshold: int = 4,
    ):
        self.provider = provider
        self.tools = tools
        self.planner_model = planner_model
        self.executor_model = executor_model
        self.max_steps = max_steps
        self.compose_strategy = compose_strategy or (lambda _scope: "")
        self.use_reframe = use_reframe
        self.use_critique = use_critique
        self.use_done_check = use_done_check
        self.json_retries = json_retries
        self.ambiguity_threshold = ambiguity_threshold

    def _system(self, scope: str, base: str) -> str:
        prefix = self.compose_strategy(scope)
        return f"{prefix}\n\n{base}" if prefix else base

    def _tool_list(self) -> str:
        """Compact tool catalog — name(args) — description, grouped by category.

        Schemas are deferred: the planner can call ``tool_search`` to expand any
        tool's full JSON schema. Saves dozens of tokens per tool on small models.
        """
        catalog = self.tools.compact_catalog()
        return catalog or "(no tools)"

    async def plan(self, goal: str) -> Plan:
        sys_text = self._system("planner", PLANNER_PROMPT_TMPL.format(
            max_steps=self.max_steps, tool_list=self._tool_list()))
        data = await llm_json(
            self.provider, self.planner_model,
            system=sys_text, user=goal,
            temperature=0.2, retries=self.json_retries,
        )
        plan = Plan(**data)
        plan.steps = plan.steps[: self.max_steps]
        return plan

    async def revise_plan(self, goal: str, prev_plan: Plan, crit) -> Plan:
        issues_text = "\n".join(
            f"- [{i.severity}] step={i.step}: {i.concern} → {i.suggestion}" for i in crit.issues
        )
        sys_text = self._system("planner", PLANNER_PROMPT_TMPL.format(
            max_steps=self.max_steps, tool_list=self._tool_list()))
        user = REVISION_PROMPT_TMPL.format(
            issues=issues_text, summary=crit.summary,
            prev_plan=prev_plan.model_dump_json(indent=2),
        )
        data = await llm_json(
            self.provider, self.planner_model,
            system=sys_text, user=user,
            temperature=0.2, retries=self.json_retries,
        )
        plan = Plan(**data)
        plan.steps = plan.steps[: self.max_steps]
        return plan

    async def execute_step(self, goal: str, plan: Plan, step: PlanStep, prior: list[StepResult]) -> StepResult:
        if step.tool:
            tr = await self.tools.call(step.tool, step.arguments)
            return StepResult(step=step, tool_result=tr, notes=f"ran tool {step.tool}")
        prior_text = "\n".join(
            f"step {r.step.n} ({r.step.description}): "
            + (r.tool_result.output[:500] if r.tool_result else r.notes)
            for r in prior
        )
        req = ChatRequest(
            model=self.executor_model,
            messages=[
                Message(role="system", content=self._system("executor", EXECUTOR_PROMPT)),
                Message(role="user", content=f"goal: {goal}\n\nprior:\n{prior_text}\n\ncurrent step: {step.description}"),
            ],
            temperature=0.3,
        )
        resp = await self.provider.chat(req)
        return StepResult(step=step, notes=resp.message.content)

    async def synthesize(
        self, goal: str, plan: Plan, results: list[StepResult],
        done: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        results_text = "\n\n".join(
            f"### step {r.step.n}: {r.step.description}\n"
            + (f"tool={r.step.tool} → {r.tool_result.output[:1500] if r.tool_result else ''}" if r.tool_result else r.notes)
            for r in results
        )
        base_prompt = SYNTHESIZER_PROMPT_WITH_DONE if done else SYNTHESIZER_PROMPT_BASE
        sys_text = self._system("synthesizer", base_prompt)
        user_parts = [f"goal: {goal}", f"plan: {plan.model_dump_json()}", f"results:\n{results_text}"]
        if done:
            user_parts.append(f"done_check:\n{json.dumps(done, indent=2)}")
        req = ChatRequest(
            model=self.planner_model,
            messages=[
                Message(role="system", content=sys_text),
                Message(role="user", content="\n\n".join(user_parts)),
            ],
            temperature=0.4,
        )
        async for chunk in self.provider.stream(req):
            if chunk.delta:
                yield chunk.delta

    async def run(self, goal: str) -> AsyncIterator[dict[str, Any]]:
        # Read stamps persist across runs intentionally: the Edit tool
        # double-checks mtime, so a stale stamp is harmless if the file
        # hasn't changed and self-corrects if it has. Clearing here would
        # break runs where a `read` returned a cached result (no fresh
        # stamp) and the model immediately tries to `edit`.

        todos = TodoList()
        # Snapshot helper: yields a 'todos' event with the current list.
        def snap() -> dict[str, Any]:
            return {"type": "todos", "todos": todos.model_dump_payload()}

        # ── meta: reframe ────────────────────────────────────
        if self.use_reframe:
            try:
                rf = await reframe(self.provider, self.planner_model, goal, retries=self.json_retries)
                yield {"type": "reframe", "reframe": rf.model_dump()}
                if rf.ambiguity >= self.ambiguity_threshold and rf.clarifying_question:
                    yield {
                        "type": "clarification_needed",
                        "ambiguity": rf.ambiguity,
                        "question": rf.clarifying_question,
                        "assumptions": rf.assumptions,
                    }
                    yield {"type": "done", "answer": rf.clarifying_question}
                    return
                # If reframe restated the goal more concretely, use that as the operational goal
                operational_goal = rf.restated_goal or goal
            except Exception as e:
                yield {"type": "warning", "stage": "reframe", "error": str(e)}
                operational_goal = goal
        else:
            operational_goal = goal

        # ── plan ──────────────────────────────────────────────
        try:
            plan = await self.plan(operational_goal)
        except Exception as e:
            yield {"type": "error", "error": f"planner failed: {e}"}
            return
        yield {"type": "plan", "plan": plan.model_dump()}
        todos.seed_from_plan_steps([s.model_dump() for s in plan.steps])
        yield snap()

        # ── meta: critique + optional one-shot revision ──────
        if self.use_critique:
            try:
                crit = await critique(self.provider, self.planner_model, operational_goal, plan.model_dump(), retries=self.json_retries)
                yield {"type": "critique", "critique": crit.model_dump()}
                if crit.verdict == "revise":
                    revised = await self.revise_plan(operational_goal, plan, crit)
                    plan = revised
                    yield {"type": "plan_revised", "plan": plan.model_dump()}
                    todos.seed_from_plan_steps([s.model_dump() for s in plan.steps])
                    yield snap()
            except Exception as e:
                yield {"type": "warning", "stage": "critique", "error": str(e)}

        # ── execute ──────────────────────────────────────────
        results: list[StepResult] = []
        for step in plan.steps:
            todos.update_status(step.n, "in_progress")
            yield snap()
            yield {"type": "step_start", "step": step.model_dump()}
            res = await self.execute_step(operational_goal, plan, step, results)
            results.append(res)
            # Mark completed/blocked based on tool result if present.
            if res.tool_result is not None and not res.tool_result.ok:
                todos.update_status(step.n, "blocked", note=(res.tool_result.error or "")[:120])
            else:
                todos.update_status(step.n, "completed")
            yield snap()
            yield {"type": "step", "result": res.model_dump()}

        # ── meta: done check ─────────────────────────────────
        done_payload: dict[str, Any] | None = None
        if self.use_done_check:
            try:
                dc = await done_check(
                    self.provider, self.planner_model,
                    operational_goal, plan.model_dump(),
                    [r.model_dump() for r in results],
                    retries=self.json_retries,
                )
                done_payload = dc.model_dump()
                yield {"type": "done_check", "done_check": done_payload}
                # If done-check says partial/failed, surface gaps as new todos.
                if dc.overall != "complete":
                    for gap in dc.gaps[:5]:
                        todos.add(f"GAP: {gap}", status="blocked")
                    yield snap()
            except Exception as e:
                yield {"type": "warning", "stage": "done_check", "error": str(e)}

        # ── synthesize ───────────────────────────────────────
        final: list[str] = []
        async for tok in self.synthesize(operational_goal, plan, results, done=done_payload):
            final.append(tok)
            yield {"type": "token", "delta": tok}
        yield {"type": "done", "answer": "".join(final), "todos": todos.model_dump_payload()}
