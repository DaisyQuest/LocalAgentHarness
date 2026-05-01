from __future__ import annotations

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

from ..engine import Engine


class LocalAgentTUI(App):
    """Minimal Textual front-end. Uses Textual's design tokens
    (``$accent``, ``$panel``, ``$text-muted``) so the TUI re-themes with the
    user's terminal color palette. Keep CSS small — this is a fallback surface."""

    CSS = """
    Screen { layout: vertical; background: $background; }
    #log {
        border: round $accent;
        padding: 1 2;
        background: $panel-darken-1;
    }
    #status {
        dock: bottom;
        height: 1;
        color: $text-muted;
        background: $panel;
        padding: 0 2;
    }
    #input {
        dock: bottom;
        border: round $primary-lighten-2;
        background: $panel;
    }
    Header { background: $primary; }
    Footer { background: $panel; }
    """
    BINDINGS = [
        Binding("ctrl+c", "quit", "quit"),
        Binding("ctrl+n", "new", "new chat"),
        Binding("ctrl+k", "clear_log", "clear"),
    ]

    engine: Engine
    cid: str

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="log", wrap=True, markup=True, highlight=True)
        yield Static("ready · ctrl+n new · ctrl+k clear · ctrl+c quit", id="status")
        yield Input(placeholder="message…  (shift+enter for newline)", id="input")
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "LocalAgent"
        self.sub_title = "local LLM harness"
        self.engine = Engine()
        self.cid = self.engine.new_conversation()
        self._log(f"[bold $accent]LocalAgent[/]  [dim]· new conversation {self.cid[:8]}[/]")
        self._log("[dim]Type a message; ctrl+n for a new chat; ctrl+k to clear; ctrl+c to quit.[/]\n")

    async def on_unmount(self) -> None:
        await self.engine.close()

    def _log(self, text: str) -> None:
        self.query_one("#log", RichLog).write(text)

    def action_new(self) -> None:
        self.cid = self.engine.new_conversation()
        self._log(f"\n[dim]── new conversation {self.cid[:8]} ──[/]\n")

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()
        self._log(f"[dim]conversation {self.cid[:8]} (cleared display only)[/]")

    @on(Input.Submitted, "#input")
    async def _submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        self._log(f"\n[bold green]you ›[/] {text}")
        self._log("[bold magenta]llm ›[/] ")
        self._stream(text)

    @work(exclusive=True)
    async def _stream(self, text: str) -> None:
        log = self.query_one("#log", RichLog)
        status = self.query_one("#status", Static)
        status.update("● streaming…")
        try:
            async for delta in self.engine.send(self.cid, text):
                log.write(delta, expand=False)
            status.update("ready · ctrl+n new · ctrl+k clear · ctrl+c quit")
        except Exception as e:
            log.write(f"\n[bold red]error:[/] {e}\n")
            status.update(f"error: {type(e).__name__}")
