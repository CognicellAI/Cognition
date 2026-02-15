"""Cognition CLI Client.

A lightweight CLI for interacting with the Cognition Agent Substrate.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
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
LOG_DIR = Path.home() / ".cognition" / "logs"


class SSEParser:
    """Parser for Server-Sent Events (SSE).

    Correctly handles multi-line data and paired event/data lines.
    """

    def __init__(self):
        self._current_event = None
        self._current_data = []

    def parse_line(self, line: str) -> Optional[tuple[str, dict]]:
        """Parse a single line of SSE stream.

        Returns:
            Tuple of (event_type, data_dict) if an event is complete, else None.
        """
        line = line.strip()
        if not line:
            # Empty line signals end of event
            if self._current_event and self._current_data:
                try:
                    data = json.loads("".join(self._current_data))
                    event = (self._current_event, data)
                    self._current_event = None
                    self._current_data = []
                    return event
                except json.JSONDecodeError:
                    self._current_event = None
                    self._current_data = []
            return None

        if line.startswith("event: "):
            self._current_event = line[7:].strip()
        elif line.startswith("data: "):
            self._current_data.append(line[6:].strip())

        return None


def get_server_url() -> str:
    """Get the server URL from environment or default."""
    return os.getenv("COGNITION_SERVER_URL", DEFAULT_SERVER_URL)


def ensure_engine() -> None:
    """Ensure the Cognition engine (server) is running."""
    url = get_server_url()

    # Try to connect to existing server
    try:
        with httpx.Client(timeout=1.0) as client:
            response = client.get(f"{url}/ready")
            if response.status_code == 200 and response.json().get("ready"):
                return  # Engine is already running and ready
    except (httpx.ConnectError, httpx.TimeoutException):
        pass

    # Engine not found or not ready, try to start it
    console.print("[yellow]Cognition Engine not found. Starting in background...[/yellow]")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "engine.log"

    # Spawn the server process
    try:
        with open(log_file, "a") as f:
            subprocess.Popen(
                [sys.executable, "-m", "server.app.cli", "serve"],
                stdout=f,
                stderr=f,
                start_new_session=True,
                env=os.environ.copy(),
            )
    except Exception as e:
        console.print(f"[bold red]✗ Failed to spawn engine:[/bold red] {e}")
        raise typer.Exit(1)

    # Wait for engine to become ready
    with Live(Text("Waiting for engine to initialize..."), refresh_per_second=4) as live:
        start_time = time.time()
        timeout = 20
        while time.time() - start_time < timeout:
            try:
                with httpx.Client(timeout=1.0) as client:
                    response = client.get(f"{url}/ready")
                    if response.status_code == 200 and response.json().get("ready"):
                        live.update(Text("✓ Engine Ready!", style="bold green"))
                        time.sleep(1)
                        return
            except Exception:
                pass
            time.sleep(1)
            live.update(
                Text(f"Waiting for engine... ({int(time.time() - start_time)}s)", style="yellow")
            )

        live.update(Text("✗ Engine startup timed out.", style="bold red"))
        console.print(f"Check logs at: {log_file}")
        raise typer.Exit(1)


@app.command("stop")
def stop_engine():
    """Stop the background Cognition engine."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/IM", "uvicorn.exe"], capture_output=True)
        else:
            subprocess.run(["pkill", "-f", "server.app.cli serve"], capture_output=True)
        console.print("[bold green]✓ Cognition engine stopped.[/bold green]")
    except Exception as e:
        console.print(f"[bold red]✗ Error stopping engine:[/bold red] {e}")


@app.command("status")
def engine_status():
    """Check the status of the Cognition engine."""
    url = get_server_url()
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(f"{url}/health")
            if resp.status_code == 200:
                data = resp.json()
                console.print(
                    Panel(
                        f"Status: [bold green]Running[/bold green]\n"
                        f"Version: {data.get('version')}\n"
                        f"Active Sessions: {data.get('active_sessions')}",
                        title="Cognition Engine Status",
                        border_style="green",
                    )
                )
            else:
                console.print(f"[yellow]Engine responding with status {resp.status_code}[/yellow]")
    except Exception:
        console.print("[bold red]Engine Status: Not Running[/bold red]")


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
    ensure_engine()
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
    ensure_engine()
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
    """Stream a chat message and display tokens with high-fidelity word-by-word printing."""
    url = f"{get_server_url()}/sessions/{session_id}/messages"

    accumulated_text = ""
    parser = SSEParser()

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

                async for line in response.aiter_lines():
                    event = parser.parse_line(line)
                    if not event:
                        continue

                    event_type, data = event

                    if event_type == "token":
                        content = data.get("content", "")
                        accumulated_text += content
                        # Direct write to stdout for highest fidelity streaming
                        sys.stdout.write(content)
                        sys.stdout.flush()

                    elif event_type == "tool_call":
                        console.print(
                            f"\n[italic yellow]🔧 Executing {data['name']}...[/italic yellow]"
                        )
                        # Show args if helpful
                        # console.print(f"[dim]{data['args']}[/dim]")

                    elif event_type == "tool_result":
                        output = data.get("output", "")
                        if len(output) > 200:
                            output = output[:200] + "..."
                        console.print(f" [dim green]→ Result: {output.strip()}[/dim green]")

                    elif event_type == "error":
                        console.print(f"\n[bold red]Agent Error:[/bold red] {data['message']}")

                    elif event_type == "done":
                        # Add a final newline
                        print("\n")
                        # Clear the raw stream and render final Markdown for reading
                        # console.clear() # Maybe too disruptive? Let's just print Markdown Panel
                        console.print(Panel(Markdown(accumulated_text), border_style="blue"))

    except Exception as e:
        console.print(f"\n[bold red]Connection Error:[/bold red] {e}")


@app.command("chat")
def chat(
    message: Optional[str] = typer.Argument(None, help="Message to send"),
    session_id: Optional[str] = typer.Option(None, "--session", "-s", help="Session ID"),
    interactive: bool = typer.Option(True, "--interactive/--single", help="Interactive mode"),
):
    """Enter interactive chat mode or send a single message."""
    ensure_engine()

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
        from client.cli.shell import CognitionShell

        shell = CognitionShell(
            session_id=active_session_id, server_url=get_server_url(), stream_chat_fn=stream_chat
        )

        asyncio.run(shell.run())


def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
