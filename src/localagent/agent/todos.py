"""Live todo list — the agent's running scratchpad of work.

The plan is the agreed contract; todos track in-flight reality. The planner
seeds the list from the initial plan; the executor and meta-cognition passes
mutate it (mark step done, add a follow-up, flag a blocker). Each mutation
emits a ``todos`` event so the UI can re-render the list as a checklist.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TodoStatus = Literal["pending", "in_progress", "completed", "blocked"]


class TodoItem(BaseModel):
    n: int
    content: str
    status: TodoStatus = "pending"
    note: str = ""

    def display(self) -> str:
        marks = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]", "blocked": "[!]"}
        return f"{marks.get(self.status, '[?]')} {self.n}. {self.content}" + (f" — {self.note}" if self.note else "")


class TodoList(BaseModel):
    """Mutable todo list with monotonic numbering."""

    items: list[TodoItem] = Field(default_factory=list)

    def seed_from_plan_steps(self, steps: list[dict[str, Any]]) -> None:
        self.items = []
        for s in steps:
            self.items.append(TodoItem(n=int(s.get("n", len(self.items) + 1)), content=str(s.get("description", "")), status="pending"))

    def update_status(self, n: int, status: TodoStatus, *, note: str = "") -> bool:
        for it in self.items:
            if it.n == n:
                it.status = status
                if note:
                    it.note = note
                return True
        return False

    def add(self, content: str, *, status: TodoStatus = "pending", note: str = "") -> TodoItem:
        next_n = (max((it.n for it in self.items), default=0) + 1)
        item = TodoItem(n=next_n, content=content, status=status, note=note)
        self.items.append(item)
        return item

    def progress(self) -> dict[str, int]:
        out: dict[str, int] = {"pending": 0, "in_progress": 0, "completed": 0, "blocked": 0}
        for it in self.items:
            out[it.status] = out.get(it.status, 0) + 1
        out["total"] = len(self.items)
        return out

    def render(self) -> str:
        return "\n".join(it.display() for it in self.items) or "(no todos)"

    def model_dump_payload(self) -> dict[str, Any]:
        return {"items": [it.model_dump() for it in self.items], "progress": self.progress()}
