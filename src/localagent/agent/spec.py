"""Spec-Driven Development data model.

A ``Spec`` captures a feature request after the user and agent have
interrogated each other to ironclad clarity. It splits into ``WorkChunk``s
(bite-sized, acceptance-tested units of work) which the agent executes
sequentially. Acceptance criteria are first-class — every chunk and the
spec as a whole have explicit pass/fail conditions verified against the
real workspace, not just the model's own claims.

Models here are pure data + serialization helpers. The orchestrator lives
in ``spec_driven.py``; persistence in ``spec_store.py``.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

ChunkStatus = Literal["pending", "in_progress", "completed", "blocked", "skipped"]
SpecStatus = Literal["draft", "ready", "executing", "verified", "partial", "failed"]


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return (s or "spec")[:48]


class AcceptanceCriterion(BaseModel):
    """One pass/fail condition. ``verification`` describes how to check it
    in the real workspace (e.g. "grep for X", "file Y exists with header Z")
    — the verifier translates this into actual tool calls."""

    id: str
    text: str
    verification: str = ""
    met: bool | None = None
    evidence: str = ""


class ClarifyingQuestion(BaseModel):
    """One ranked question. Constrained types only — binary, multiple-choice,
    or name-the-value — to keep small models from generating vague garbage."""

    n: int
    text: str
    why: str = ""
    importance: int = 3  # 1 (nice-to-know) … 5 (blocker)
    kind: Literal["binary", "choice", "value"] = "value"
    choices: list[str] = Field(default_factory=list)  # populated for kind=choice
    answer: str | None = None  # filled in by integrate_answers


class WorkChunk(BaseModel):
    """One bite-sized unit. Description should be one or two sentences;
    file_hints are the planner's crutch for grep/glob targeting."""

    n: int
    title: str
    description: str
    file_hints: list[str] = Field(default_factory=list)
    acceptance: list[AcceptanceCriterion] = Field(default_factory=list)
    status: ChunkStatus = "pending"
    notes: str = ""
    attempts: int = 0
    last_error: str = ""

    def progress_line(self) -> str:
        marks = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]",
                 "blocked": "[!]", "skipped": "[-]"}
        return f"{marks.get(self.status, '[?]')} {self.n}. {self.title}"


class SpecReadiness(BaseModel):
    score: int = 1  # 1 (vague) … 5 (ironclad)
    ready: bool = False
    blockers: list[str] = Field(default_factory=list)
    summary: str = ""


class SpecVerification(BaseModel):
    overall: Literal["verified", "partial", "failed"] = "partial"
    chunks_completed: int = 0
    chunks_total: int = 0
    criteria_met: int = 0
    criteria_total: int = 0
    gaps: list[str] = Field(default_factory=list)


class Spec(BaseModel):
    """The full spec. Mutable across phases. Persist after every transition
    so a Ctrl-C never loses more than one phase of work."""

    id: str
    title: str
    goal: str = ""  # the original user prompt, kept verbatim for grounding
    summary: str = ""  # the agreed "north star" — one paragraph
    requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    open_questions: list[ClarifyingQuestion] = Field(default_factory=list)
    work_chunks: list[WorkChunk] = Field(default_factory=list)
    global_acceptance: list[AcceptanceCriterion] = Field(default_factory=list)
    history: list[str] = Field(default_factory=list)  # human-readable audit log
    readiness: SpecReadiness | None = None
    verification: SpecVerification | None = None
    status: SpecStatus = "draft"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    rounds: int = 0  # how many interrogation rounds have happened

    # ── factories ────────────────────────────────────────────
    @classmethod
    def new(cls, title: str, goal: str) -> "Spec":
        return cls(
            id=f"{_slug(title)}-{uuid.uuid4().hex[:6]}",
            title=title.strip() or "untitled",
            goal=goal.strip(),
            summary="",
        )

    # ── helpers ──────────────────────────────────────────────
    def touch(self) -> None:
        self.updated_at = time.time()

    def log(self, line: str) -> None:
        self.history.append(line)
        self.touch()

    def context_preamble(self, *, max_chunks_in_history: int = 4) -> str:
        """Compact spec context to wrap around a chunk's goal.

        Capped budget: title + summary + requirements + constraints +
        out-of-scope + the last few completed chunks (one line each) +
        a count of any older ones. Designed to fit in <800 chars on most
        specs so a 7B-model context isn't dominated by it.
        """
        lines: list[str] = [f"# Spec: {self.title}"]
        if self.summary:
            lines.append(self.summary.strip())
        if self.requirements:
            lines.append("## Requirements\n" + "\n".join(f"- {r}" for r in self.requirements))
        if self.constraints:
            lines.append("## Constraints\n" + "\n".join(f"- {c}" for c in self.constraints))
        if self.out_of_scope:
            lines.append("## Out of scope\n" + "\n".join(f"- {x}" for x in self.out_of_scope))

        completed = [c for c in self.work_chunks if c.status == "completed"]
        if completed:
            tail = completed[-max_chunks_in_history:]
            elided = len(completed) - len(tail)
            lines.append("## Already completed")
            if elided > 0:
                lines.append(f"- (+{elided} earlier chunks completed)")
            for c in tail:
                note = c.notes.strip().splitlines()[0][:120] if c.notes else "done"
                lines.append(f"- {c.n}. {c.title} — {note}")
        return "\n\n".join(lines)

    def chunk_by_n(self, n: int) -> WorkChunk | None:
        for c in self.work_chunks:
            if c.n == n:
                return c
        return None

    def progress(self) -> dict[str, int]:
        out: dict[str, int] = {"pending": 0, "in_progress": 0, "completed": 0,
                               "blocked": 0, "skipped": 0}
        for c in self.work_chunks:
            out[c.status] = out.get(c.status, 0) + 1
        out["total"] = len(self.work_chunks)
        return out
