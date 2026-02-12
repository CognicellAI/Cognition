"""Textual TUI client for Cognition."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import websockets
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Input,
    Label,
    RichLog,
    Static,
)

from client.tui.api import ApiClient
from shared import (
    CreateProject,
    CreateSession,
    Done,
    Error,
    ProjectCreated,
    SessionStarted,
    Token,
    ToolCall,
    ToolResult,
    UserMessage,
    message_to_json,
    parse_message,
)


class MessageDisplay(Static):
    """Display for a single message."""

    def __init__(self, role: str, content: str, **kwargs):
        super().__init__(**kwargs)
        self.role = role
        self.content = content

    def compose(self) -> ComposeResult:
        if self.role == "user":
            yield Label(f"You: {self.content}", classes="user-message")
        else:
            yield Label(f"Agent: {self.content}", classes="agent-message")


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

    def __init__(self, server_url: str = "ws://127.0.0.1:8000"):
        super().__init__()
        self.server_url = server_url
        self.ws_url = server_url.replace("http", "ws") + "/ws"
        self.api = ApiClient(server_url.replace("ws", "http").rstrip("/ws"))
        self.websocket: websockets.WebSocketClientProtocol | None = None
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
        if self.websocket:
            await self.websocket.close()
        await self.api.close()

    async def connect(self) -> None:
        """Connect to the server and create session."""
        try:
            # Connect WebSocket
            self.websocket = await websockets.connect(self.ws_url)
            self.connected = True
            self.update_status()

            # Create project
            await self.websocket.send(message_to_json(CreateProject(user_prefix="tui-session")))

            response = await self.websocket.recv()
            msg = parse_message(response)

            if isinstance(msg, ProjectCreated):
                self.project_id = msg.project_id
                self.log_message(f"Created project: {msg.project_id}")

                # Create session
                await self.websocket.send(message_to_json(CreateSession(project_id=msg.project_id)))

                response = await self.websocket.recv()
                msg = parse_message(response)

                if isinstance(msg, SessionStarted):
                    self.session_id = msg.session_id
                    self.log_message(f"Started session: {msg.session_id}")
                    self.update_status()

                    # Start listening for messages
                    asyncio.create_task(self.listen())
                elif isinstance(msg, Error):
                    self.log_message(f"Error: {msg.message}", error=True)
            elif isinstance(msg, Error):
                self.log_message(f"Error: {msg.message}", error=True)

        except Exception as e:
            self.log_message(f"Connection failed: {e}", error=True)
            self.connected = False
            self.update_status()

    async def listen(self) -> None:
        """Listen for incoming WebSocket messages."""
        try:
            while self.websocket:
                message = await self.websocket.recv()
                msg = parse_message(message)
                await self.handle_message(msg)
        except websockets.exceptions.ConnectionClosed:
            self.log_message("Connection closed")
            self.connected = False
            self.update_status()
        except Exception as e:
            self.log_message(f"Listen error: {e}", error=True)

    async def handle_message(self, msg: Any) -> None:
        """Handle incoming message from server."""
        if isinstance(msg, Token):
            # Accumulate tokens
            self.current_response += msg.content
        elif isinstance(msg, ToolCall):
            # Show tool call
            if self.current_response:
                self.log_message(self.current_response)
                self.current_response = ""
            self.log_message(f"🛠️  {msg.name}({msg.args})", tool=True)
        elif isinstance(msg, ToolResult):
            # Show tool result
            output = msg.output[:200] + "..." if len(msg.output) > 200 else msg.output
            self.log_message(f"📤 Result: {output}", tool_result=True)
        elif isinstance(msg, Done):
            # Response complete
            if self.current_response:
                self.log_message(self.current_response)
                self.current_response = ""
            self.enable_input()
        elif isinstance(msg, Error):
            self.log_message(f"❌ Error: {msg.message}", error=True)
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
        if not self.websocket or not self.session_id:
            self.log_message("Not connected", error=True)
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

        # Send to server
        try:
            await self.websocket.send(
                message_to_json(
                    UserMessage(
                        session_id=self.session_id,
                        content=message,
                    )
                )
            )
        except Exception as e:
            self.log_message(f"Failed to send: {e}", error=True)
            self.enable_input()


def main():
    """Entry point for the TUI client."""
    server_url = os.getenv("COGNITION_SERVER_URL", "ws://127.0.0.1:8000")
    app = CognitionApp(server_url=server_url)
    app.run()


if __name__ == "__main__":
    main()
