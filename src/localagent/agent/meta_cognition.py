"""Meta-cognition passes — the reasoning habits that lift weaker models.

Three passes wrap the planner-executor:

* ``reframe(goal)`` — restate the goal, list assumptions, flag ambiguity. If
  ambiguity is high, the agent should ask a clarifying question instead of
  building a plan on guesswork.
* ``critique(goal, plan)`` — independent reviewer scrutinizes the plan for
  missing verification, scope creep, risky operations, and wrong-tool choices.
  Returns issues plus a verdict; the planner gets one revision budget.
* ``done_check(goal, plan, results)`` — explicit per-criterion check at the end.
  Forces the synthesizer to admit gaps rather than confabulate completion.

All three return structured JSON; they share a bounded-retry helper that feeds
parse failures back to the model so transient malformations self-correct.
"""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from ..core import ChatRequest, Message, Provider


# ── JSON helpers with bounded retry ──────────────────────────
_JSON_RE = re.compile(r"\{.*\}", re.S)


def _extract_json(text: str) -> dict[str, Any]:
    m = _JSON_RE.search(text)
    if not m:
        raise ValueError("no JSON object found in model output")
    return json.loads(m.group(0))


async def llm_json(
    provider: Provider,
    model: str,
    *,
    system: str,
    user: str,
    temperature: float = 0.2,
    retries: int = 2,
) -> dict[str, Any]:
    """Call an LLM expecting JSON; on parse failure, feed the error back and retry."""
    last_err: str | None = None
    last_raw: str | None = None
    for attempt in range(retries + 1):
        prompt = user
        if last_err is not None:
            prompt = (
                f"{user}\n\n---\nYour previous attempt was malformed: {last_err}\n"
                f"Previous output (first 300 chars): {(last_raw or '')[:300]}\n"
                "Return STRICT JSON only this time. No prose, no markdown fences."
            )
        try:
            resp = await provider.chat(ChatRequest(
                model=model,
                messages=[Message(role="system", content=system), Message(role="user", content=prompt)],
                temperature=temperature,
            ))
            last_raw = resp.message.content
            return _extract_json(last_raw)
        except (json.JSONDecodeError, ValueError) as e:
            last_err = f"{type(e).__name__}: {str(e)[:200]}"
            continue
    raise ValueError(f"failed after {retries + 1} attempts: {last_err}")


# ── reframe ──────────────────────────────────────────────────
class Reframe(BaseModel):
    restated_goal: str
    assumptions: list[str] = Field(default_factory=list)
    ambiguity: int = 1  # 1-5
    clarifying_question: str | None = None


REFRAME_PROMPT = """You're orienting before doing real work. Given a user's request:

1. Restate it in your own words, concrete and unambiguous
2. List assumptions you'd have to make to act on it (3-7 bullets)
3. Score ambiguity 1-5 where 1 = perfectly clear and 5 = need clarification before any work
4. If ambiguity >= 4, propose ONE clarifying question to ask the user

Return STRICT JSON only:
{"restated_goal": "...", "assumptions": ["...", "..."], "ambiguity": 1-5, "clarifying_question": "..." or null}

EXAMPLE input:
"fix the bug in auth"

EXAMPLE output:
{"restated_goal": "Investigate and fix a bug in authentication code", "assumptions": ["the codebase has a recognizable auth module", "the bug is reproducible or has clear symptoms", "the user means the current branch's state"], "ambiguity": 5, "clarifying_question": "Which specific symptom are you seeing — failed logins, token expiration, or something else?"}

Now process the actual request."""


async def reframe(provider: Provider, model: str, goal: str, *, retries: int = 2) -> Reframe:
    data = await llm_json(
        provider, model,
        system=REFRAME_PROMPT, user=goal,
        temperature=0.1, retries=retries,
    )
    return Reframe(**data)


# ── critique ─────────────────────────────────────────────────
class CritiqueIssue(BaseModel):
    severity: str = "low"  # low | medium | high
    step: int | None = None
    concern: str
    suggestion: str = ""


class Critique(BaseModel):
    issues: list[CritiqueIssue] = Field(default_factory=list)
    verdict: str = "ship"  # ship | revise
    summary: str = ""


CRITIQUE_PROMPT = """You are an independent reviewer. The planner produced a plan; find what's wrong with it BEFORE execution.

Look for:
- Missing verification steps (asserting facts about files/symbols without checking they exist)
- Scope creep (steps that go beyond what was asked)
- Wrong tool for a step (e.g. shell_exec for what file_read could do)
- Destructive operations without an explicit safety check
- Steps whose output is never used downstream
- Order-of-operations bugs (using something before producing it)
- Vague step descriptions that the executor can't act on

For each issue, set severity (low|medium|high), the step number (or null if structural), the concern, and a concrete suggestion.

Set verdict to "revise" only if there's at least one HIGH severity issue. Otherwise "ship".

Return STRICT JSON only:
{"issues": [{"severity":"...","step":<n or null>,"concern":"...","suggestion":"..."}], "verdict":"ship|revise", "summary":"<one sentence>"}"""


async def critique(provider: Provider, model: str, goal: str, plan: dict[str, Any], *, retries: int = 2) -> Critique:
    user = f"goal: {goal}\n\nproposed plan:\n{json.dumps(plan, indent=2)}"
    data = await llm_json(
        provider, model,
        system=CRITIQUE_PROMPT, user=user,
        temperature=0.2, retries=retries,
    )
    return Critique(**data)


# ── done check ───────────────────────────────────────────────
class CriterionResult(BaseModel):
    description: str
    met: bool
    evidence: str = ""


class DoneCheck(BaseModel):
    criteria: list[CriterionResult] = Field(default_factory=list)
    overall: str = "complete"  # complete | partial | failed
    gaps: list[str] = Field(default_factory=list)


DONE_CHECK_PROMPT = """You verify whether the agent actually accomplished the goal. Be honest, not generous.

Given the goal, the executed plan, and step results:
1. Derive 2-5 criteria that "done" requires for THIS goal
2. For each criterion, decide met/not-met based on the evidence in step results
3. List concrete gaps if anything is missing
4. Overall: "complete" only if every criterion is met; "partial" if some met; "failed" if none

Do NOT invent evidence. If the results don't show something, the criterion isn't met.

Return STRICT JSON only:
{"criteria":[{"description":"...","met":true,"evidence":"<quote or step ref>"}], "overall":"complete|partial|failed", "gaps":["..."]}"""


async def done_check(
    provider: Provider, model: str,
    goal: str, plan: dict[str, Any], results: list[dict[str, Any]],
    *, retries: int = 2,
) -> DoneCheck:
    summary = []
    for r in results:
        step = r.get("step", {})
        line = f"step {step.get('n', '?')}: {step.get('description', '')}"
        if r.get("tool_result"):
            tr = r["tool_result"]
            line += f" → tool={step.get('tool')} ok={tr.get('ok')} output={(tr.get('output') or tr.get('error') or '')[:300]}"
        elif r.get("notes"):
            line += f" → notes: {r['notes'][:300]}"
        summary.append(line)
    user = f"goal: {goal}\n\nplan: {json.dumps(plan)}\n\nresults:\n" + "\n".join(summary)
    data = await llm_json(
        provider, model,
        system=DONE_CHECK_PROMPT, user=user,
        temperature=0.2, retries=retries,
    )
    return DoneCheck(**data)
