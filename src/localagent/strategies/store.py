"""Strategies: named master-context blocks injected into agent system prompts.

Each strategy lives as a single markdown file with YAML-ish frontmatter under
``~/.localagent/strategies/<slug>.md``. The store hot-reads on every access so
the user can edit files in place.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Literal

import yaml
from pydantic import BaseModel, Field

from .project_context import discover_project_context, format_project_context

Scope = Literal["chat", "planner", "executor", "synthesizer", "all"]
ALL_SCOPES: tuple[Scope, ...] = ("chat", "planner", "executor", "synthesizer", "all")


class Strategy(BaseModel):
    id: str
    name: str
    description: str = ""
    scopes: list[Scope] = Field(default_factory=lambda: ["all"])
    active: bool = True
    body: str = ""

    def applies_to(self, scope: Scope) -> bool:
        if not self.active:
            return False
        return "all" in self.scopes or scope in self.scopes


_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.strip().lower()).strip("-")
    return s or "strategy"


def _parse(path: Path) -> Strategy:
    raw = path.read_text(encoding="utf-8")
    m = _FM_RE.match(raw)
    if m:
        meta = yaml.safe_load(m.group(1)) or {}
        body = m.group(2).strip()
    else:
        meta, body = {}, raw.strip()
    return Strategy(
        id=path.stem,
        name=meta.get("name", path.stem),
        description=meta.get("description", ""),
        scopes=meta.get("scopes", ["all"]),
        active=bool(meta.get("active", True)),
        body=body,
    )


def _serialize(s: Strategy) -> str:
    fm = yaml.safe_dump(
        {"name": s.name, "description": s.description, "scopes": s.scopes, "active": s.active},
        sort_keys=False,
    ).strip()
    return f"---\n{fm}\n---\n\n{s.body.strip()}\n"


SEED_STRATEGIES: dict[str, str] = {
    "verify-before-claim": _serialize(Strategy(
        id="verify-before-claim",
        name="Verify Before Claim",
        description="Don't assert facts about files, symbols, or APIs without checking. Prefer cheap probes.",
        scopes=["planner", "executor", "chat"],
        active=True,
        body=(
            "Before asserting a file exists, a symbol is defined, an API behaves a certain way, or memory recall "
            "is current, do the cheapest possible verification first.\n"
            "- File path? Check it exists before reading.\n"
            "- Function name? grep before referencing.\n"
            "- Memory says X? Confirm X is still true now — memories can be stale.\n"
            "- Library/method behavior? Read the docstring or a test, don't infer from the name.\n"
            "If verification isn't possible in the current context, FLAG the assumption explicitly rather than "
            "silently relying on it."
        ),
    )),
    "scope-discipline": _serialize(Strategy(
        id="scope-discipline",
        name="Scope Discipline",
        description="Match action to ask. No gold-plating, no speculative refactors, no helpers for hypothetical futures.",
        scopes=["planner", "executor", "chat"],
        active=True,
        body=(
            "Match the scope of your work to what was actually requested.\n"
            "- A bug fix doesn't need a refactor.\n"
            "- A one-shot operation doesn't need a reusable helper.\n"
            "- Three similar lines is better than a premature abstraction.\n"
            "- Don't add error handling for scenarios that can't happen. Trust internal callers.\n"
            "- Don't validate inputs that came from another part of the same codebase. Validate at boundaries.\n"
            "- Don't introduce backwards-compat shims when you can just change the code.\n"
            "If you find yourself producing more than the user asked for, stop and ship the smaller version."
        ),
    )),
    "name-the-conflict": _serialize(Strategy(
        id="name-the-conflict",
        name="Name the Conflict",
        description="When evidence and reasoning point different ways, surface the conflict — don't silently pick.",
        scopes=["chat", "executor", "synthesizer"],
        active=True,
        body=(
            "When two pieces of input disagree — retrieved memory vs. fresh observation, instructions vs. code, "
            "user request vs. project constraint — call it out explicitly.\n"
            'Format: "I see a conflict: <X says A>, but <Y says B>. I\'m going with <choice> because <reason>." '
            "Or, if the conflict blocks progress, ask the user which to trust.\n"
            "Never silently pick a side and pretend the other input didn't exist."
        ),
    )),
    "stop-when-done": _serialize(Strategy(
        id="stop-when-done",
        name="Stop When Done",
        description="Know when 'done' is achieved. Don't loop, don't gold-plate, don't trail with summaries.",
        scopes=["executor", "synthesizer", "chat"],
        active=True,
        body=(
            "Before each new action, ask: have I already accomplished what was requested?\n"
            "- If yes: stop. Do not summarize what's already visible to the user.\n"
            "- If a step failed twice with the same error: change approach, don't retry the same way.\n"
            "- Don't append 'let me know if you want me to also...' offers unless the follow-up is genuinely valuable.\n"
            "- End-of-turn = at most one or two sentences. The diff/output speaks for itself."
        ),
    )),
    "code-quality": _serialize(Strategy(
        id="code-quality",
        name="Code Quality Guardrails",
        description="Defaults that lift weaker code models toward correctness.",
        scopes=["executor", "synthesizer", "chat"],
        active=False,
        body=(
            "When producing code:\n"
            "- Prefer the smallest correct change; avoid speculative refactors.\n"
            "- Validate inputs at boundaries; trust internal callers.\n"
            "- No silent fallbacks. Raise on failure with actionable messages.\n"
            "- Preserve public APIs unless explicitly asked to change them.\n"
            "- Match the surrounding code style (indentation, naming, imports).\n"
            "- Never invent functions, libraries, or signatures. If unsure, state the assumption.\n"
            "- For Python: type hints, no bare except, prefer dataclasses/pydantic over dicts for structure.\n"
        ),
    )),
    "structured-thinking": _serialize(Strategy(
        id="structured-thinking",
        name="Structured Thinking",
        description="Force smaller models into explicit, verifiable reasoning before answering.",
        scopes=["planner", "executor"],
        active=False,
        body=(
            "Before producing output, briefly: (1) restate the goal in your own words, "
            "(2) list assumptions, (3) outline the approach as 1–5 bullets. Then answer.\n"
            "Keep this preamble compact (under 80 words). Then commit to the actual output."
        ),
    )),
    "spec-mode-discipline": _serialize(Strategy(
        id="spec-mode-discipline",
        name="Spec-Mode Discipline",
        description="Active during spec-driven runs: stick to the chunk; verify; don't expand scope.",
        scopes=["planner", "executor", "synthesizer"],
        active=True,
        body=(
            "When a goal arrives wrapped in <spec_context>, you are running ONE chunk of a larger "
            "spec-driven plan.\n"
            "- Touch only files relevant to THIS chunk. Don't refactor adjacent code, don't 'while we're here'.\n"
            "- The chunk has explicit acceptance criteria with prescribed verification methods. Plan steps "
            "  that produce evidence those criteria can be checked against (e.g., the grep target should "
            "  actually exist after your edit).\n"
            "- If a requirement seems impossible, do not silently drop it. Report it as a blocker; the "
            "  orchestrator will surface it.\n"
            "- Do NOT re-litigate the spec itself. Open questions were resolved in the interrogation phase."
        ),
    )),
}


class StrategyStore:
    def __init__(self, dir_path: Path, *, project_root: Path | None = None):
        self.dir = Path(dir_path)
        self.dir.mkdir(parents=True, exist_ok=True)
        # The directory we walk upward from to find a LOCALAGENT.md /
        # AGENTS.md project-context file. Defaults to the cwd at start time
        # (consistent with how Claude Code resolves CLAUDE.md).
        self.project_root: Path = (project_root or Path.cwd()).resolve()
        self._seed_if_empty()

    def set_project_root(self, root: Path) -> None:
        self.project_root = Path(root).resolve()

    def project_context(self) -> tuple[Path, str] | None:
        """Hot-read the project-context file (if any) on each access."""
        return discover_project_context(self.project_root)

    def _seed_if_empty(self) -> None:
        if not any(self.dir.glob("*.md")):
            for slug, content in SEED_STRATEGIES.items():
                (self.dir / f"{slug}.md").write_text(content, encoding="utf-8")

    def list(self) -> list[Strategy]:
        out: list[Strategy] = []
        for p in sorted(self.dir.glob("*.md")):
            try:
                out.append(_parse(p))
            except Exception:
                continue
        return out

    def get(self, sid: str) -> Strategy | None:
        p = self.dir / f"{sid}.md"
        return _parse(p) if p.exists() else None

    def upsert(self, s: Strategy) -> Strategy:
        if not s.id:
            s.id = _slugify(s.name)
        (self.dir / f"{s.id}.md").write_text(_serialize(s), encoding="utf-8")
        return s

    def delete(self, sid: str) -> bool:
        p = self.dir / f"{sid}.md"
        if p.exists():
            p.unlink()
            return True
        return False

    def set_active(self, sid: str, active: bool) -> Strategy | None:
        s = self.get(sid)
        if not s:
            return None
        s.active = active
        return self.upsert(s)

    def compose(self, scope: Scope, *, extra: Iterable[Strategy] = ()) -> str:
        parts: list[str] = []
        for s in list(self.list()) + list(extra):
            if s.applies_to(scope):
                parts.append(f"### {s.name}\n{s.body.strip()}")
        master = ""
        if parts:
            master = "<master_context>\n" + "\n\n".join(parts) + "\n</master_context>"
        # Always layer the project-context file on top so workspace-specific
        # guidance (LOCALAGENT.md / AGENTS.md) reaches every scope.
        proj = self.project_context()
        if proj is not None:
            block = format_project_context(*proj)
            if block:
                return f"{block}\n\n{master}".strip()
        return master
