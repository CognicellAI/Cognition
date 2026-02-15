"""Cognition Interactive Shell.

Provides a rich REPL for interacting with the Cognition Agent Substrate.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from client.cli.main import stream_chat

console = Console()

HISTORY_FILE = Path.home() / ".cognition" / "history"


class CognitionShell:
    """Interactive REPL for Cognition."""

    def __init__(self, session_id: str, server_url: str, stream_chat_fn: Any):
        """Initialize the shell.

        Args:
            session_id: The active session ID.
            server_url: The engine server URL.
            stream_chat_fn: The async function to call for streaming chat.
        """
        self.session_id = session_id
        self.server_url = server_url
        self.stream_chat_fn = stream_chat_fn

        # Ensure history file exists
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

        self.session = PromptSession(
            history=FileHistory(str(HISTORY_FILE)),
        )

    async def run(self):
        """Run the interactive shell loop."""
        console.print(
            Panel(
                Text.assemble(
                    ("Connected to Session: ", "white"),
                    (self.session_id, "bold cyan"),
                    ("\nType ", "white"),
                    ("/help", "bold yellow"),
                    (" for commands or ", "white"),
                    ("exit", "bold red"),
                    (" to quit.", "white"),
                ),
                title="[bold blue]Cognition Agent Substrate Shell[/bold blue]",
                border_style="blue",
            )
        )

        while True:
            try:
                # Use a simple formatted prompt string
                prompt_text = f"cognition:{self.session_id[:8]} > "
                user_input = await self.session.prompt_async(prompt_text)

                user_input = user_input.strip()
                if not user_input:
                    continue

                # Handle Slash Commands
                if user_input.startswith("/"):
                    if await self.handle_command(user_input):
                        continue
                    else:
                        break  # Exit signaled

                # Normal Chat Message
                await self.stream_chat_fn(self.session_id, user_input)
                print()  # Add spacing

            except KeyboardInterrupt:
                # Ctrl+C clears the line but keeps the shell open
                continue
            except EOFError:
                # Ctrl+D exits
                break
            except Exception as e:
                console.print(f"[bold red]Shell Error:[/bold red] {e}")

    async def handle_command(self, cmd_str: str) -> bool:
        """Handle slash commands.

        Returns:
            True to continue shell, False to exit.
        """
        parts = cmd_str.split()
        cmd = parts[0].lower()

        if cmd in ["/exit", "/quit"]:
            return False

        if cmd == "/help":
            self.show_help()
        elif cmd == "/clear":
            console.clear()
        elif cmd == "/session":
            console.print(f"Active Session: [bold cyan]{self.session_id}[/bold cyan]")
        elif cmd == "/status":
            from client.cli.main import engine_status

            engine_status()
        else:
            console.print(f"[yellow]Unknown command: {cmd}. Type /help for list.[/yellow]")

        return True

    def show_help(self):
        """Display available slash commands."""
        table = Table(title="Shell Commands", show_header=False, border_style="dim")
        table.add_row("/help", "Show this help menu")
        table.add_row("/clear", "Clear the terminal screen")
        table.add_row("/session", "Show current session info")
        table.add_row("/status", "Check engine health")
        table.add_row("/exit", "Exit the shell")
        console.print(table)
