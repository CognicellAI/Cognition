"""Cognition CLI Client.

A lightweight CLI for interacting with the Cognition Agent Substrate.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import httpx
import typer
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

app = typer.Typer(
    name="cognition-cli",
    help="CLI client for the Cognition Agent Substrate",
    no_args_is_help=True,
)
session_app = typer.Typer(help="Manage agent sessions")
app.add_typer(session_app, name="session")

console = Console()

# Configuration
DEFAULT_SERVER_URL = "http://localhost:8000"
STATE_FILE = Path(".cognition-cli-state.json")


def get_server_url() -> str:
    """Get the server URL from environment or default."""
    return os.getenv("COGNITION_SERVER_URL", DEFAULT_SERVER_URL)


def save_state(session_id: str) -> None:
    """Save current session ID to state file."""
    state = {"current_session_id": session_id}
    STATE_FILE.write_text(json.dumps(state))


def load_state() -> dict:
    """Load state from file."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


@session_app.command("create")
def create_session(
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Session title"),
):
    """Create a new agent session."""
    url = f"{get_server_url()}/sessions"

    try:
        response = httpx.post(url, json={"title": title})
        response.raise_for_status()
        data = response.json()

        session_id = data["id"]
        save_state(session_id)

        console.print(f"[bold green]✓ Session created:[/bold green] {session_id}")
        if title:
            console.print(f"Title: {title}")
    except Exception as e:
        console.print(f"[bold red]✗ Failed to create session:[/bold red] {e}")
        raise typer.Exit(1)


@session_app.command("list")
def list_sessions():
    """List all sessions in the current workspace."""
    url = f"{get_server_url()}/sessions"

    try:
        response = httpx.get(url)
        response.raise_for_status()
        data = response.json()

        sessions = data.get("sessions", [])
        if not sessions:
            console.print("No active sessions found.")
            return

        table = Table(title="Cognition Sessions")
        table.add_column("ID", style="cyan")
        table.add_column("Title", style="green")
        table.add_column("Messages", justify="right")
        table.add_column("Updated", style="magenta")

        current_id = load_state().get("current_session_id")

        for s in sessions:
            marker = "*" if s["id"] == current_id else " "
            table.add_row(
                f"{marker}{s['id'][:8]}",
                s.get("title") or "Untitled",
                str(s.get("message_count", 0)),
                s.get("updated_at", "")[:16].replace("T", " "),
            )

        console.print(table)
        console.print("\n* = Current session")
    except Exception as e:
        console.print(f"[bold red]✗ Failed to list sessions:[/bold red] {e}")
        raise typer.Exit(1)


async def stream_chat(session_id: str, message: str):
    """Stream a chat message and display tokens."""
    url = f"{get_server_url()}/sessions/{session_id}/messages"

    accumulated_text = ""

    # We'll use a Panel for the agent's response
    # to separate it clearly from the user's input

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", url, json={"content": message}) as response:
                if response.status_code != 200:
                    error_data = await response.aread()
                    try:
                        error_json = json.loads(error_data)
                        msg = error_json.get("detail", error_data.decode())
                    except:
                        msg = error_data.decode()
                    console.print(f"[bold red]Error:[/bold red] {msg}")
                    return

                console.print(f"\n[bold cyan]Agent Substrate:[/bold cyan]")

                with Live(Markdown(""), refresh_per_second=10, auto_refresh=False) as live:
                    event_type = None
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue

                        if line.startswith("event: "):
                            event_type = line[7:].strip()
                        elif line.startswith("data: "):
                            data_str = line[6:].strip()
                            try:
                                data = json.loads(data_str)

                                if event_type == "token":
                                    accumulated_text += data.get("content", "")
                                    live.update(Markdown(accumulated_text))
                                    live.refresh()
                                elif event_type == "tool_call":
                                    # Temporarily pause live display to print tool info
                                    live.stop()
                                    args_str = json.dumps(data.get("args", {}), indent=2)
                                    console.print(
                                        Panel(
                                            Text(
                                                f"Executing {data['name']}...", style="bold yellow"
                                            ),
                                            subtitle=args_str,
                                            border_style="yellow",
                                            padding=(0, 1),
                                        )
                                    )
                                    live.start()
                                elif event_type == "tool_result":
                                    live.stop()
                                    output = data.get("output", "")
                                    # Show a snippet of output if it's long
                                    if len(output) > 200:
                                        output = output[:200] + "..."
                                    console.print(
                                        f" [dim green]→ Result: {output.strip()}[/dim green]"
                                    )
                                    live.start()
                                elif event_type == "error":
                                    live.stop()
                                    console.print(
                                        f"\n[bold red]Agent Error:[/bold red] {data['message']}"
                                    )
                                    live.start()
                                elif event_type == "done":
                                    live.update(Markdown(accumulated_text))
                                    live.refresh()

                            except json.JSONDecodeError:
                                pass
    except Exception as e:
        console.print(f"\n[bold red]Connection Error:[/bold red] {e}")


@app.command("chat")
def chat(
    message: Optional[str] = typer.Argument(None, help="Message to send"),
    session_id: Optional[str] = typer.Option(None, "--session", "-s", help="Session ID"),
    interactive: bool = typer.Option(True, "--interactive/--single", help="Interactive mode"),
):
    """Enter interactive chat mode or send a single message."""
    # Check for stdin piping
    piped_data = ""
    if not sys.stdin.isatty():
        piped_data = sys.stdin.read().strip()

    state = load_state()
    active_session_id = session_id or state.get("current_session_id")

    if not active_session_id:
        console.print("[yellow]No active session found. Creating one...[/yellow]")
        url = f"{get_server_url()}/sessions"
        try:
            response = httpx.post(url, json={"title": "CLI Auto-Created"})
            response.raise_for_status()
            active_session_id = response.json()["id"]
            save_state(active_session_id)
            console.print(f"[bold green]✓ Session created:[/bold green] {active_session_id}")
        except Exception as e:
            console.print(f"[bold red]✗ Failed to auto-create session:[/bold red] {e}")
            raise typer.Exit(1)

    if message or piped_data:
        # Construct message from argument + piped data
        full_message = message or ""
        if piped_data:
            full_message = f"{full_message}\n\n[Piped Data]:\n{piped_data}"

        asyncio.run(stream_chat(active_session_id, full_message))
        return

    if interactive:
        console.print(
            Panel(
                f"Interactive Session: [bold cyan]{active_session_id}[/bold cyan]\n"
                "Type 'exit' or 'quit' to end.",
                title="Cognition Agent Substrate",
                border_style="blue",
            )
        )

        while True:
            try:
                user_input = typer.prompt("You")
                if user_input.lower() in ["exit", "quit"]:
                    break

                asyncio.run(stream_chat(active_session_id, user_input))
                print()
            except typer.Abort:
                break


def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
