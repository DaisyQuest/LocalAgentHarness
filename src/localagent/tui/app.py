from __future__ import annotations

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

from ..engine import Engine


class LocalAgentTUI(App):
    CSS = """
    Screen { layout: vertical; }
    #log { border: round $accent; padding: 1 2; }
    #input { dock: bottom; }
    #status { dock: bottom; height: 1; color: $text-muted; padding: 0 1; }
    """
    BINDINGS = [
        Binding("ctrl+c", "quit", "quit"),
        Binding("ctrl+n", "new", "new chat"),
    ]

    engine: Engine
    cid: str

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="log", wrap=True, markup=True, highlight=True)
        yield Static("ready", id="status")
        yield Input(placeholder="message… (ctrl+n new, ctrl+c quit)", id="input")
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "LocalAgent"
        self.engine = Engine()
        self.cid = self.engine.new_conversation()
        self._log(f"[dim]new conversation {self.cid[:8]}[/]")

    async def on_unmount(self) -> None:
        await self.engine.close()

    def _log(self, text: str) -> None:
        self.query_one("#log", RichLog).write(text)

    def action_new(self) -> None:
        self.cid = self.engine.new_conversation()
        self._log(f"\n[dim]── new conversation {self.cid[:8]} ──[/]\n")

    @on(Input.Submitted, "#input")
    async def _submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        self._log(f"[bold green]you ›[/] {text}")
        self._log("[bold magenta]llm ›[/] ")
        self._stream(text)

    @work(exclusive=True)
    async def _stream(self, text: str) -> None:
        log = self.query_one("#log", RichLog)
        status = self.query_one("#status", Static)
        status.update("streaming…")
        async for delta in self.engine.send(self.cid, text):
            log.write(delta, expand=False)
        status.update("ready")
