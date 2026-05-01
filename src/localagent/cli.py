from __future__ import annotations

import asyncio
import json
from typing import Any

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax
from rich.table import Table

from .config import settings
from .engine import Engine

app = typer.Typer(no_args_is_help=True, add_completion=False, help="LocalAgent — local LLM harness")
rag_app = typer.Typer(no_args_is_help=True, help="RAG: ingest and inspect documents.")
agent_app = typer.Typer(no_args_is_help=True, help="Planner-executor agent commands.")
mem_app = typer.Typer(no_args_is_help=True, help="Long-term memory.")
strat_app = typer.Typer(no_args_is_help=True, help="Master-context strategies.")
spec_app = typer.Typer(no_args_is_help=True, help="Spec-driven development: ironclad spec → bite-sized chunks → verified.")
app.add_typer(rag_app, name="rag")
app.add_typer(agent_app, name="agent")
app.add_typer(mem_app, name="memory")
app.add_typer(strat_app, name="strategy")
app.add_typer(spec_app, name="spec")
console = Console()


def _interactive_confirmer():
    async def confirm(name: str, args: dict[str, Any]) -> bool:
        console.print(Panel(
            f"[yellow]tool:[/] [bold]{name}[/]\n[yellow]args:[/]\n"
            f"{Syntax(json.dumps(args, indent=2), 'json', theme='ansi_dark', line_numbers=False).code if False else json.dumps(args, indent=2)}",
            title="confirm tool call", border_style="yellow",
        ))
        return Confirm.ask(f"approve {name}?", default=False)
    return confirm


@app.command()
def chat(
    role: str = typer.Option("auto", help="auto|chat|code|fast or model name"),
    rag: bool = typer.Option(False, help="Inject retrieved RAG context per turn"),
    memory: bool = typer.Option(True, help="Recall long-term memory each turn"),
    cid: str | None = typer.Option(None, "--cid", help="Resume conversation by id"),
):
    asyncio.run(_chat(role=role, rag=rag, memory=memory, cid=cid))


async def _chat(role: str, rag: bool, memory: bool, cid: str | None) -> None:
    engine = Engine()
    try:
        if cid is None:
            cid = engine.new_conversation()
            console.print(Panel.fit(
                f"new conversation [bold]{cid[:8]}[/]  ·  role={role}  ·  rag={rag}  ·  memory={memory}",
                style="cyan",
            ))
        while True:
            try:
                user = console.input("[bold green]you ›[/] ")
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]bye.[/]")
                return
            if not user.strip():
                continue
            if user.strip() in {"/exit", "/quit"}:
                return
            if user.strip().startswith("/remember "):
                text = user.strip()[len("/remember "):]
                mid = await engine.memory.remember(text, kind="user")
                console.print(f"[dim]remembered {mid[:8]}[/]")
                continue
            console.print("[bold magenta]llm ›[/] ", end="")
            async for delta in engine.send(cid, user, role=role, use_rag=rag, use_memory=memory):
                console.print(delta, end="", soft_wrap=True, highlight=False)
            console.print()
    finally:
        await engine.close()


@app.command()
def models():
    asyncio.run(_models())


async def _models() -> None:
    engine = Engine()
    try:
        names = await engine.provider.list_models()
        if not names:
            console.print("[yellow]no models found.[/] is Ollama running?")
            return
        for n in names:
            console.print(f"  • {n}")
    finally:
        await engine.close()


@app.command()
def conversations():
    engine = Engine()
    try:
        for c in engine.store.list_conversations():
            console.print(f"  {c['id'][:8]}  {c.get('title') or '(untitled)'}")
    finally:
        engine.store.close()


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000):
    import uvicorn
    uvicorn.run("localagent.server.app:app", host=host, port=port, reload=False)


@app.command()
def tui():
    from .tui.app import LocalAgentTUI
    LocalAgentTUI().run()


@app.command()
def info():
    console.print(Panel(Markdown(f"```json\n{settings.model_dump_json(indent=2)}\n```"), title="settings"))


# ── rag ──────────────────────────────────────────────────────
@rag_app.command("ingest")
def rag_ingest(path: str = typer.Argument(..., help="File or folder path, or URL")):
    asyncio.run(_rag_ingest(path))


async def _rag_ingest(path: str) -> None:
    engine = Engine()
    try:
        if path.startswith(("http://", "https://")):
            did = await engine.rag_ingest_url(path)
            console.print(f"[green]ingested[/] {did[:8]}  ←  {path}")
        else:
            ids = await engine.rag_ingest_path(path)
            console.print(f"[green]ingested[/] {len(ids)} document(s)")
    finally:
        await engine.close()


@rag_app.command("list")
def rag_list():
    engine = Engine()
    try:
        t = Table(title="RAG documents")
        t.add_column("id"); t.add_column("kind"); t.add_column("title"); t.add_column("source")
        for d in engine.vector_store.list_documents():
            t.add_row(d["id"][:8], d["kind"], d.get("title") or "", d["source"])
        console.print(t)
    finally:
        engine.vector_store.close()


@rag_app.command("search")
def rag_search(query: str, k: int = 5):
    asyncio.run(_rag_search(query, k))


async def _rag_search(query: str, k: int) -> None:
    engine = Engine()
    try:
        hits = await engine.retriever.retrieve(query, k=k)
        for h in hits:
            console.print(Panel(h["content"][:400], title=f"{h.get('title') or h['source']}  d={h['distance']:.3f}"))
    finally:
        await engine.close()


# ── agent ────────────────────────────────────────────────────
@agent_app.command("run")
def agent_run(goal: str, yes: bool = typer.Option(False, "--yes", "-y", help="auto-approve all tools")):
    asyncio.run(_agent_run(goal, yes))


async def _agent_run(goal: str, yes: bool) -> None:
    engine = Engine()
    try:
        if yes:
            engine.tools.set_confirmer(lambda n, a: asyncio.sleep(0, result=True) if False else _yes())
        else:
            engine.tools.set_confirmer(_interactive_confirmer())
        async for ev in engine.agent_run(goal):
            t = ev["type"]
            if t == "plan":
                console.print(Panel(
                    Markdown(f"```json\n{json.dumps(ev['plan'], indent=2)}\n```"),
                    title="plan", style="cyan",
                ))
            elif t == "todos":
                items = ev["todos"]["items"]
                p = ev["todos"]["progress"]
                marks = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]", "blocked": "[!]"}
                lines = [f"{marks.get(it['status'], '[?]')} {it['n']}. {it['content']}"
                         + (f"  [dim]— {it['note']}[/]" if it.get("note") else "")
                         for it in items]
                summary = f"{p['completed']}/{p['total']} done · {p['blocked']} blocked"
                console.print(Panel("\n".join(lines), title=f"todos ({summary})", border_style="blue"))
            elif t == "step_start":
                s = ev["step"]
                console.print(f"[bold cyan]· step {s['n']}[/] {s['description']}"
                              + (f"  [dim](tool: {s['tool']})[/]" if s.get("tool") else ""))
            elif t == "step":
                r = ev["result"]
                if r.get("tool_result"):
                    tr = r["tool_result"]
                    out = tr.get("output") or tr.get("error") or ""
                    style = "green" if tr.get("ok") else "red"
                    cached = " (cached)" if tr.get("meta", {}).get("cached") else ""
                    console.print(Panel(out[:1500], title=f"tool: {r['step']['tool']}{cached}", border_style=style))
                elif r.get("notes"):
                    console.print(f"  [dim]{r['notes'][:300]}[/]")
            elif t == "token":
                console.print(ev["delta"], end="", soft_wrap=True, highlight=False)
            elif t == "done":
                console.print()
            elif t == "error":
                console.print(f"[red]error:[/] {ev['error']}")
    finally:
        await engine.close()


async def _yes() -> bool:
    return True


# ── memory ───────────────────────────────────────────────────
@mem_app.command("add")
def mem_add(text: str, kind: str = "fact"):
    asyncio.run(_mem_add(text, kind))


async def _mem_add(text: str, kind: str) -> None:
    engine = Engine()
    try:
        mid = await engine.memory.remember(text, kind=kind)
        console.print(f"[green]stored[/] {mid[:8]}")
    finally:
        await engine.close()


@mem_app.command("list")
def mem_list():
    engine = Engine()
    try:
        t = Table(title="long-term memory")
        t.add_column("id"); t.add_column("kind"); t.add_column("text")
        for m in engine.memory.list():
            t.add_row(m["id"][:8], m["kind"], m["text"][:80])
        console.print(t)
    finally:
        engine.memory.close()


@mem_app.command("search")
def mem_search(query: str, k: int = 5):
    asyncio.run(_mem_search(query, k))


async def _mem_search(query: str, k: int) -> None:
    engine = Engine()
    try:
        for h in await engine.memory.recall(query, k=k):
            console.print(Panel(h["text"], title=f"{h['kind']}  d={h['distance']:.3f}"))
    finally:
        await engine.close()


@mem_app.command("forget")
def mem_forget(mid: str):
    engine = Engine()
    try:
        engine.memory.forget(mid)
        console.print(f"[red]forgot[/] {mid[:8]}")
    finally:
        engine.memory.close()


# ── strategies ───────────────────────────────────────────────
@strat_app.command("list")
def strat_list():
    engine = Engine()
    try:
        t = Table(title="strategies")
        t.add_column("id"); t.add_column("name"); t.add_column("scopes"); t.add_column("active")
        for s in engine.strategies.list():
            t.add_row(s.id, s.name, ",".join(s.scopes), "✓" if s.active else "·")
        console.print(t)
        console.print(f"[dim]files: {engine.strategies.dir}[/]")
    finally:
        pass


@strat_app.command("show")
def strat_show(sid: str):
    engine = Engine()
    s = engine.strategies.get(sid)
    if not s:
        console.print(f"[red]not found:[/] {sid}")
        return
    console.print(Panel(Markdown(s.body), title=f"{s.name} · scopes={s.scopes} · active={s.active}"))


@strat_app.command("activate")
def strat_activate(sid: str):
    Engine().strategies.set_active(sid, True)
    console.print(f"[green]activated[/] {sid}")


@strat_app.command("deactivate")
def strat_deactivate(sid: str):
    Engine().strategies.set_active(sid, False)
    console.print(f"[yellow]deactivated[/] {sid}")


@strat_app.command("delete")
def strat_delete(sid: str):
    Engine().strategies.delete(sid)
    console.print(f"[red]deleted[/] {sid}")


@strat_app.command("preview")
def strat_preview(scope: str = "chat"):
    text = Engine().strategies.compose(scope)  # type: ignore[arg-type]
    console.print(Panel(text or "[dim](no active strategies for this scope)[/]", title=f"preview: {scope}"))


@strat_app.command("edit")
def strat_edit():
    """Open the strategies folder in the OS file manager."""
    import os
    import subprocess
    d = Engine().strategies.dir
    console.print(f"strategies dir: [cyan]{d}[/]")
    try:
        if os.name == "nt":
            os.startfile(str(d))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(d)])
    except Exception as e:
        console.print(f"[yellow]could not open file manager: {e}[/]")


# ── spec-driven ───────────────────────────────────────────────


@spec_app.command("list")
def spec_list():
    engine = Engine()
    try:
        rows = engine.spec_store.list()
        if not rows:
            console.print("[dim](no specs yet — try `localagent spec start \"<goal>\"`)[/]")
            return
        t = Table(title="specs")
        t.add_column("id"); t.add_column("title"); t.add_column("status")
        t.add_column("rounds"); t.add_column("chunks")
        for r in rows:
            t.add_row(r["id"], r["title"][:50], r["status"], str(r["rounds"]), str(r["chunks"]))
        console.print(t)
        console.print(f"[dim]files: {engine.spec_store.dir}[/]")
    finally:
        engine.store.close()


@spec_app.command("show")
def spec_show(sid: str):
    engine = Engine()
    try:
        spec = engine.spec_store.load(sid)
        if not spec:
            console.print(f"[red]not found:[/] {sid}")
            return
        _render_spec(spec)
    finally:
        engine.store.close()


@spec_app.command("delete")
def spec_delete(sid: str):
    engine = Engine()
    try:
        if engine.spec_store.delete(sid):
            console.print(f"[red]deleted[/] {sid}")
        else:
            console.print(f"[yellow]not found:[/] {sid}")
    finally:
        engine.store.close()


@spec_app.command("start")
def spec_start(
    goal: str = typer.Argument(..., help="Free-form feature request"),
    max_rounds: int = typer.Option(3, "--max-rounds", help="Max interrogation rounds"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve risky tools during execution"),
    skip_interrogation: bool = typer.Option(False, "--ship-it", help="Skip interrogation; treat draft as ready"),
):
    """Start a fresh spec-driven run, interrogate the user, then execute."""
    asyncio.run(_spec_run(goal, sid=None, max_rounds=max_rounds, yes=yes, skip=skip_interrogation))


@spec_app.command("resume")
def spec_resume(
    sid: str,
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Resume a saved spec from wherever it left off."""
    asyncio.run(_spec_run(goal=None, sid=sid, max_rounds=3, yes=yes, skip=False))


async def _spec_run(*, goal: str | None, sid: str | None, max_rounds: int, yes: bool, skip: bool) -> None:
    engine = Engine()
    engine.spec_agent.config.max_rounds = max_rounds
    try:
        if sid is None:
            assert goal is not None
            console.print(Panel(f"[cyan]drafting spec from goal…[/]\n{goal}", title="spec/start"))
            spec = await engine.spec_start(goal)
            _render_spec(spec, compact=True)
        else:
            spec = engine.spec_store.load(sid)
            if not spec:
                console.print(f"[red]not found:[/] {sid}")
                return
            console.print(Panel(f"[cyan]resuming[/] {spec.id} · status={spec.status}", title="spec/resume"))

        # ── interrogation loop ───────────────────────────────
        if not skip and spec.status not in ("ready", "executing", "verified"):
            for _round in range(max_rounds):
                console.print(f"\n[bold]round {spec.rounds + 1}/{max_rounds}[/]  ·  asking clarifying questions…")
                spec, questions = await engine.spec_questions(spec.id)
                if not questions:
                    console.print("[green]no further questions — spec looks clear.[/]")
                    break
                console.print(Panel(
                    "\n".join(_format_question(q) for q in questions),
                    title=f"{len(questions)} question(s)", border_style="yellow",
                ))
                console.print("[dim]Answer each (or type /ship to lock the spec, /skip to skip a question):[/]")
                answers: list[dict] = []
                user_shipped = False
                for q in questions:
                    label = f"Q{q.n} [{q.kind}, importance={q.importance}]"
                    if q.kind == "choice" and q.choices:
                        label += f" {{{ '|'.join(q.choices) }}}"
                    try:
                        ans = console.input(f"[bold green]{label} ›[/] ")
                    except (EOFError, KeyboardInterrupt):
                        console.print("\n[dim]aborted[/]")
                        return
                    if ans.strip() == "/ship":
                        user_shipped = True
                        break
                    if ans.strip() in {"/skip", ""}:
                        continue
                    answers.append({"n": q.n, "answer": ans.strip()})
                if user_shipped:
                    engine.spec_force_ready(spec.id, reason="user typed /ship")
                    spec = engine.spec_store.load(spec.id)
                    break
                if answers:
                    spec = await engine.spec_answer(spec.id, answers)
                    _render_spec(spec, compact=True)
                # readiness check
                spec, readiness = await engine.spec_readiness(spec.id)
                console.print(f"[dim]readiness: {readiness.score}/5 — {readiness.summary}[/]")
                if readiness.ready:
                    console.print("[green]spec is ironclad. moving on.[/]")
                    break
            else:
                # exhausted rounds without readiness
                if spec.readiness and not spec.readiness.ready:
                    proceed = Confirm.ask(
                        f"reached round cap ({max_rounds}) without readiness ≥ {engine.spec_agent.config.readiness_threshold}. "
                        "ship it anyway?", default=True,
                    )
                    if not proceed:
                        console.print("[yellow]aborting — spec saved at " + spec.id + "[/]")
                        return
                    engine.spec_force_ready(spec.id, reason="round cap reached")
                    spec = engine.spec_store.load(spec.id)

        # ── decompose ────────────────────────────────────────
        if not spec.work_chunks:
            console.print("\n[cyan]decomposing into chunks…[/]")
            spec = await engine.spec_decompose(spec.id)
        console.print(Panel("\n".join(c.progress_line() for c in spec.work_chunks)
                             + (f"\n\nGlobal acceptance ({len(spec.global_acceptance)}):\n"
                                + "\n".join(f"  · {ac.text}" for ac in spec.global_acceptance) if spec.global_acceptance else ""),
                            title=f"plan · {len(spec.work_chunks)} chunks", border_style="cyan"))

        # ── execute ──────────────────────────────────────────
        async def confirmer(name: str, args: dict[str, Any]) -> bool:
            if yes:
                return True
            return await _interactive_confirmer()(name, args)
        engine.tools.set_confirmer(confirmer)

        async for ev in engine.spec_execute(spec.id):
            _render_event(ev)
        # final state
        spec = engine.spec_store.load(spec.id)
        if spec and spec.verification:
            v = spec.verification
            color = {"verified": "green", "partial": "yellow", "failed": "red"}.get(v.overall, "white")
            console.print(Panel(
                f"[{color}]{v.overall.upper()}[/]  ·  chunks {v.chunks_completed}/{v.chunks_total}"
                f"  ·  criteria {v.criteria_met}/{v.criteria_total}\n"
                + ("\nGaps:\n" + "\n".join(f"  · {g}" for g in v.gaps) if v.gaps else ""),
                title=f"spec verified — {spec.id}", border_style=color,
            ))
    finally:
        await engine.close()


def _format_question(q) -> str:
    pre = f"  Q{q.n} [imp={q.importance}, {q.kind}] {q.text}"
    if q.kind == "choice" and q.choices:
        pre += f"\n     choices: {' | '.join(q.choices)}"
    if q.why:
        pre += f"\n     [dim]why: {q.why}[/]"
    return pre


def _render_spec(spec, *, compact: bool = False) -> None:
    body = []
    if spec.summary:
        body.append(f"[bold]summary[/] · {spec.summary}")
    if spec.requirements:
        body.append("[bold]requirements[/]\n" + "\n".join(f"  · {r}" for r in spec.requirements))
    if spec.constraints:
        body.append("[bold]constraints[/]\n" + "\n".join(f"  · {c}" for c in spec.constraints))
    if spec.out_of_scope:
        body.append("[bold]out of scope[/]\n" + "\n".join(f"  · {x}" for x in spec.out_of_scope))
    if not compact and spec.work_chunks:
        body.append("[bold]chunks[/]\n" + "\n".join(f"  {c.progress_line()}" for c in spec.work_chunks))
    if not compact and spec.global_acceptance:
        body.append("[bold]global acceptance[/]\n" + "\n".join(
            f"  · [{('x' if ac.met else (' ' if ac.met is None else '!'))}] {ac.text}"
            for ac in spec.global_acceptance))
    console.print(Panel("\n\n".join(body) or "(empty)",
                        title=f"{spec.id}  ·  {spec.status}", border_style="cyan"))


def _render_event(ev: dict) -> None:
    t = ev.get("type")
    if t == "chunk_start":
        c = ev["chunk"]
        attempt = ev.get("attempt", 1)
        suffix = f" (retry {attempt})" if attempt > 1 else ""
        console.print(f"\n[bold cyan]▶ chunk {c['n']}/?[/] [bold]{c['title']}[/]{suffix}")
        console.print(f"  [dim]{c['description']}[/]")
    elif t == "chunk_done":
        c = ev["chunk"]
        console.print(f"  [green]✓ done[/] — {c.get('notes', '')[:120]}")
    elif t == "chunk_blocked":
        c = ev["chunk"]
        console.print(f"  [red]✗ blocked[/] — {c.get('last_error', '')[:200]}")
    elif t == "chunk_retry":
        console.print(f"  [yellow]retrying chunk {ev.get('chunk_n')}[/]")
    elif t == "criterion_verified":
        cr = ev["criterion"]
        mark = "[green]✓[/]" if cr.get("met") else "[red]✗[/]"
        console.print(f"    {mark} {cr.get('text', '')[:100]}  [dim]· {cr.get('evidence', '')[:120]}[/]")
    elif t == "plan":
        console.print(f"    [dim]plan: {len(ev['plan'].get('steps', []))} steps[/]")
    elif t == "step_start":
        s = ev["step"]
        console.print(f"    · {s['n']}. {s['description'][:80]}"
                      + (f" [dim](tool: {s['tool']})[/]" if s.get("tool") else ""))
    elif t == "step":
        r = ev["result"]
        if r.get("tool_result"):
            tr = r["tool_result"]
            if not tr.get("ok"):
                console.print(f"      [red]tool error:[/] {tr.get('error', '')[:200]}")
    elif t == "spec_verified":
        # rendered separately by caller
        pass
    elif t == "warning":
        console.print(f"    [yellow]warn[/] [{ev.get('stage')}]: {ev.get('error', '')[:200]}")


if __name__ == "__main__":
    app()
