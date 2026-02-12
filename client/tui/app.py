"""Textual TUI client for Cognition with REST + SSE.

Updated for Phase 5: Uses REST API and Server-Sent Events instead of WebSocket.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.widgets import Button, Input, Label, RichLog

from client.tui.api import ApiClient, EventHandler


class CognitionApp(App):
    """Main TUI application for Cognition."""

    CSS = """
    Screen {
        layout: vertical;
    }
    
    #chat-container {
        height: 1fr;
        border: solid green;
        padding: 1;
    }
    
    #messages {
        height: 1fr;
        overflow-y: scroll;
    }
    
    #input-container {
        height: auto;
        dock: bottom;
        padding: 1;
    }
    
    #status-bar {
        height: auto;
        dock: top;
        padding: 1;
        background: $surface-darken-1;
    }
    
    .user-message {
        color: $text-accent;
        margin: 1;
    }
    
    .agent-message {
        color: $text;
        margin: 1;
    }
    
    .tool-call {
        color: $text-warning;
        margin-left: 4;
    }
    
    .tool-result {
        color: $text-success;
        margin-left: 4;
    }
    
    .error {
        color: $text-error;
    }
    """

    # Reactive state
    session_id: reactive[str | None] = reactive(None)
    project_id: reactive[str | None] = reactive(None)
    connected: reactive[bool] = reactive(False)

    def __init__(self, server_url: str = "http://127.0.0.1:8000"):
        super().__init__()
        self.server_url = server_url
        self.api = ApiClient(server_url)
        self.current_response: str = ""
        self.messages: list[dict] = []

    def compose(self) -> ComposeResult:
        """Compose the UI."""
        with Horizontal(id="status-bar"):
            yield Label("Cognition", id="title")
            yield Label("Disconnected", id="connection-status")
            yield Label("", id="session-info")

        with Container(id="chat-container"):
            yield RichLog(id="messages", highlight=True, wrap=True)

        with Horizontal(id="input-container"):
            yield Input(placeholder="Type your message...", id="message-input")
            yield Button("Send", id="send-button")

    async def on_mount(self) -> None:
        """Called when app is mounted."""
        await self.connect()

    async def on_unmount(self) -> None:
        """Called when app is unmounted."""
        await self.api.close()

    async def connect(self) -> None:
        """Connect to the server and create project + session."""
        try:
            # Check server health
            health = await self.api.health()
            self.connected = True
            self.update_status()

            # Create project
            self.log_message("Creating project...")
            project = await self.api.create_project(
                name="tui-session",
                description="TUI session project",
            )
            self.project_id = project["id"]
            self.log_message(f"✓ Created project: {project['name']}")

            # Create session
            self.log_message("Creating session...")
            session = await self.api.create_session(
                project_id=self.project_id,
                title="TUI Session",
            )
            self.session_id = session["id"]
            self.log_message(f"✓ Started session: {session['id'][:8]}...")
            self.update_status()

            self.log_message("Ready! Type a message to start.")
            self.enable_input()

        except Exception as e:
            self.log_message(f"❌ Connection failed: {e}", error=True)
            self.connected = False
            self.update_status()

    async def handle_sse_event(self, event: dict) -> None:
        """Handle a single SSE event."""
        event_type = event.get("event")
        data = event.get("data", {})

        if event_type == "token":
            # Accumulate tokens
            self.current_response += data.get("content", "")
            # Update display in real-time
            messages = self.query_one("#messages", RichLog)
            # Clear previous partial message and rewrite
            # (In a real implementation, we'd update inline)

        elif event_type == "tool_call":
            # Show tool call
            if self.current_response:
                self.log_message(self.current_response)
                self.current_response = ""
            name = data.get("name", "unknown")
            args = data.get("args", {})
            self.log_message(f"🛠️  {name}({args})", tool=True)

        elif event_type == "tool_result":
            # Show tool result
            output = data.get("output", "")
            if len(output) > 200:
                output = output[:200] + "..."
            self.log_message(f"📤 Result: {output}", tool_result=True)

        elif event_type == "usage":
            # Show usage info
            cost = data.get("estimated_cost", 0)
            input_tokens = data.get("input_tokens", 0)
            output_tokens = data.get("output_tokens", 0)
            self.log_message(
                f"💰 Usage: ${cost:.4f} ({input_tokens} in, {output_tokens} out)",
            )

        elif event_type == "done":
            # Response complete
            if self.current_response:
                self.log_message(self.current_response)
                self.current_response = ""
            self.enable_input()

        elif event_type == "error":
            message = data.get("message", "Unknown error")
            self.log_message(f"❌ Error: {message}", error=True)
            self.enable_input()

    def log_message(
        self, content: str, error: bool = False, tool: bool = False, tool_result: bool = False
    ) -> None:
        """Log a message to the chat display."""
        messages = self.query_one("#messages", RichLog)

        if error:
            messages.write(f"[red]{content}[/red]")
        elif tool:
            messages.write(f"[yellow]{content}[/yellow]")
        elif tool_result:
            messages.write(f"[green]{content}[/green]")
        else:
            messages.write(content)

    def update_status(self) -> None:
        """Update the status bar."""
        status = self.query_one("#connection-status", Label)
        session_info = self.query_one("#session-info", Label)

        if self.connected:
            status.update("Connected")
            status.styles.color = "green"
        else:
            status.update("Disconnected")
            status.styles.color = "red"

        if self.session_id:
            session_info.update(f"Session: {self.session_id[:8]}...")

    def enable_input(self) -> None:
        """Enable the input field."""
        input_widget = self.query_one("#message-input", Input)
        input_widget.disabled = False
        input_widget.focus()

    def disable_input(self) -> None:
        """Disable the input field."""
        input_widget = self.query_one("#message-input", Input)
        input_widget.disabled = True

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        if event.button.id == "send-button":
            await self.send_message()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission."""
        if event.input.id == "message-input":
            await self.send_message()

    async def send_message(self) -> None:
        """Send a message to the agent."""
        if not self.session_id:
            self.log_message("❌ Not connected", error=True)
            return

        input_widget = self.query_one("#message-input", Input)
        message = input_widget.value.strip()

        if not message:
            return

        # Clear input and disable
        input_widget.value = ""
        self.disable_input()

        # Show user message
        self.log_message(f"You: {message}")
        self.current_response = ""

        # Send to server and stream response
        try:
            async for event in self.api.send_message(
                session_id=self.session_id,
                content=message,
            ):
                await self.handle_sse_event(event)
        except Exception as e:
            self.log_message(f"❌ Failed to send: {e}", error=True)
            self.enable_input()


def main():
    """Entry point for the TUI client."""
    server_url = os.getenv("COGNITION_SERVER_URL", "http://127.0.0.1:8000")
    # Ensure URL uses http/https, not ws
    server_url = server_url.replace("ws://", "http://").replace("wss://", "https://")
    app = CognitionApp(server_url=server_url)
    app.run()


if __name__ == "__main__":
    main()
