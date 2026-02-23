"""Cognition Interactive Shell (Cyberpunk Edition).

Provides a rich REPL for interacting with the Cognition Agent
with real-time telemetry and a high-tech dashboard layout.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    pass

console = Console()

# Cyberpunk Color Palette
NEON_PURPLE = "#BF00FF"
NEON_CYAN = "#00FFFF"
DARK_GREY = "#1A1A1A"
WARNING_YELLOW = "#FFFF00"

HISTORY_FILE = Path.home() / ".cognition" / "history"


class SessionStats:
    """Tracks real-time session telemetry."""

    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = 0.0
        self.model = "UNKNOWN"
        self.provider = "UNKNOWN"

    def update(self, data: dict):
        self.input_tokens += data.get("input_tokens", 0)
        self.output_tokens += data.get("output_tokens", 0)
        self.cost += data.get("estimated_cost", 0.0)
        self.model = data.get("model", self.model)
        self.provider = data.get("provider", self.provider)


class CognitionShell:
    """Interactive REPL for Cognition with futuristic dashboard."""

    def __init__(self, session_id: str, server_url: str, stream_chat_fn: Any):
        self.session_id = session_id
        self.server_url = server_url
        self.stream_chat_fn = stream_chat_fn
        self.stats = SessionStats()

        # Cache for model completions
        self.available_models = []

        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.session = PromptSession(
            history=FileHistory(str(HISTORY_FILE)),
        )

    def make_header(self) -> Panel:
        """Create the dashboard header."""
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="right", ratio=1)

        grid.add_row(
            Text.assemble(
                ("COGNITION_OS ", f"bold {NEON_PURPLE}"),
                ("v0.1.0", "dim"),
                (" | ", "white"),
                ("WORKSPACE: ", "bold white"),
                (os.getcwd(), NEON_CYAN),
            ),
            Text.assemble(
                ("SESSION_ID: ", "bold white"),
                (self.session_id[:8], NEON_PURPLE),
                (" [", "white"),
                ("ONLINE", "bold green"),
                ("]", "white"),
            ),
        )
        return Panel(grid, style=f"bold {NEON_PURPLE}", border_style=NEON_PURPLE)

    def make_footer(self) -> Panel:
        """Create the dashboard footer with telemetry."""
        grid = Table.grid(expand=True)
        grid.add_column(justify="left")
        grid.add_column(justify="center")
        grid.add_column(justify="right")

        grid.add_row(
            Text.assemble(
                ("MODEL: ", "bold white"),
                (self.stats.model.upper(), NEON_CYAN),
                (f" ({self.stats.provider})", "dim"),
            ),
            Text.assemble(
                ("TOKENS: ", "bold white"),
                (f"In: {self.stats.input_tokens}", "green"),
                (" | ", "white"),
                (f"Out: {self.stats.output_tokens}", "yellow"),
            ),
            Text.assemble(
                ("SESSION_COST: ", "bold white"), (f"${self.stats.cost:.4f}", "bold green")
            ),
        )
        return Panel(grid, style="white", border_style="dim")

    async def fetch_models(self):
        """Fetch available models from the engine."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.server_url}/config")
                if resp.status_code == 200:
                    data = resp.json()
                    providers = data.get("llm", {}).get("available_providers", [])
                    models = []
                    for p in providers:
                        models.extend(p.get("models", []))
                    self.available_models = models
                    self.stats.model = data.get("llm", {}).get("model", "UNKNOWN")
                    self.stats.provider = data.get("llm", {}).get("provider", "UNKNOWN")
        except Exception:
            pass

    async def switch_model(self, model_id: str):
        """Update the current session to use a different model."""
        url = f"{self.server_url}/sessions/{self.session_id}"

        try:
            async with httpx.AsyncClient() as client:
                # Update session config
                resp = await client.patch(url, json={"config": {"model": model_id}})
                resp.raise_for_status()
                self.stats.model = model_id
                console.print(f"[bold green]✓ BRAIN_LINK UPDATED: {model_id}[/bold green]")
        except Exception as e:
            console.print(f"[bold red]✗ BRAIN_LINK FAILURE:[/bold red] {e}")

    async def run(self):
        """Run the interactive shell loop."""
        await self.fetch_models()

        # Initial display
        console.clear()
        console.print(self.make_header())
        console.print(
            Panel(
                Text.assemble(
                    ("SYSTEM READY. ", "bold green"),
                    ("Enter query to begin investigation or ", "white"),
                    ("/help", f"bold {WARNING_YELLOW}"),
                    (" for command list.", "white"),
                ),
                border_style=NEON_CYAN,
            )
        )
        console.print(self.make_footer())

        # Check if we are in a TTY
        is_tty = sys.stdin.isatty() and sys.stdout.isatty()
        if not is_tty:
            console.print(
                "[yellow]Warning: Non-TTY environment detected. Interactive features may be limited.[/yellow]"
            )

        consecutive_errors = 0
        while True:
            try:
                # Completer for slash commands and models
                completer = WordCompleter(
                    ["/help", "/clear", "/session", "/status", "/exit", "/model"]
                    + self.available_models,
                    ignore_case=True,
                )

                prompt_str = f"cognition:{self.session_id[:8]} > "

                if is_tty:
                    # Use prompt_toolkit for full features in TTY
                    with patch_stdout():
                        try:
                            user_input = await self.session.prompt_async(
                                prompt_str, completer=completer
                            )
                        except OSError as e:
                            if e.errno == 22:
                                # Fallback to basic input if Errno 22 occurs
                                is_tty = False
                                raise e
                            raise e
                else:
                    # Fallback to basic input for non-TTY
                    user_input = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: input(prompt_str)
                    )

                user_input = user_input.strip()
                if not user_input:
                    continue

                if user_input.startswith("/"):
                    if await self.handle_command(user_input):
                        consecutive_errors = 0
                        continue
                    else:
                        break

                # Execute chat and update HUD on completion
                await self.stream_chat_fn(self.session_id, user_input, self.stats.update)

                # Reset error counter on success
                consecutive_errors = 0

                # Show updated telemetry
                console.print(self.make_footer())

            except (KeyboardInterrupt, asyncio.CancelledError):
                console.print("\n[yellow]Operation cancelled.[/yellow]")
                continue
            except EOFError:
                break
            except Exception as e:
                consecutive_errors += 1
                if not isinstance(e, OSError) or e.errno != 22:
                    console.print(f"[bold red]UNHANDLED_EXCEPTION:[/bold red] {e}")

                if consecutive_errors > 5:
                    console.print(
                        "[bold red]Too many consecutive errors. Exiting shell.[/bold red]"
                    )
                    break

                await asyncio.sleep(0.5)

    async def handle_command(self, cmd_str: str) -> bool:
        parts = cmd_str.split()
        cmd = parts[0].lower()

        if cmd in ["/exit", "/quit"]:
            return False

        if cmd == "/help":
            self.show_help()
        elif cmd == "/clear":
            console.clear()
            console.print(self.make_header())
        elif cmd == "/model":
            if len(parts) > 1:
                await self.switch_model(parts[1])
            else:
                console.print(f"[bold yellow]CURRENT_MODEL:[/bold yellow] {self.stats.model}")
                console.print(
                    f"[bold cyan]AVAILABLE_BRAINS:[/bold cyan] {', '.join(self.available_models)}"
                )
        elif cmd == "/session":
            console.print(f"THREAD_ID: [bold cyan]{self.session_id}[/bold cyan]")
        elif cmd == "/status":
            from client.cli.main import engine_status

            engine_status()
        else:
            console.print(f"[bold red]INVALID_COMMAND:[/bold red] {cmd}")

        return True

    def show_help(self):
        table = Table(title="COGNITION_COMMAND_INDEX", border_style=NEON_PURPLE)
        table.add_column("COMMAND", style=NEON_CYAN)
        table.add_column("DESCRIPTION", style="white")
        table.add_row("/model <id>", "Switch the active LLM for this session")
        table.add_row("/help", "Show this system index")
        table.add_row("/clear", "Reset visual buffers")
        table.add_row("/session", "Display thread telemetry")
        table.add_row("/status", "Verify engine integrity")
        table.add_row("/exit", "Terminate connection")
        console.print(table)
