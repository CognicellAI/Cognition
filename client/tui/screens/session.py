"""Session screen - main chat interface."""

from typing import TYPE_CHECKING

from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, LoadingIndicator, Static

from client.tui.websocket import (
    AssistantMessageEvent,
    DiffAppliedEvent,
    DoneEvent,
    ErrorEvent,
    ServerEvent,
    SessionStartedEvent,
    TestsFinishedEvent,
    ToolEndEvent,
    ToolOutputEvent,
    ToolStartEvent,
    WebSocketMessages,
)
from client.tui.widgets import ChatMessage, PromptInput, StatusBar, ToolBlock

if TYPE_CHECKING:
    from textual.app import ComposeResult


class SessionScreen(Screen):
    """Main chat session screen.

    Displays conversation history, tool execution blocks,
    and handles all WebSocket events.
    """

    CSS_PATH = "../styles/app.tcss"

    BINDINGS = [
        ("ctrl+c", "cancel", "Cancel"),
        ("ctrl+d", "disconnect", "Disconnect"),
        ("ctrl+q", "quit", "Quit"),
    ]

    # Session state
    session_id = reactive("")
    project_name = reactive("")
    network_mode = reactive("OFF")
    is_processing = reactive(False)

    # Track active tool blocks and current tool per session
    _active_tools: dict[str, ToolBlock] = {}
    _current_tool: dict[str, str] = {}

    def compose(self) -> "ComposeResult":
        """Compose the session screen layout."""
        yield StatusBar(id="status-bar")

        with VerticalScroll(id="chat-container"):
            # Messages will be added here dynamically
            pass

        yield PromptInput(id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize screen state."""
        self._update_status_bar()

    def watch_session_id(self) -> None:
        """Update UI when session ID changes."""
        self._update_status_bar()
        prompt = self.query_one("#prompt", PromptInput)
        prompt.set_session_active(bool(self.session_id))

    def watch_is_processing(self) -> None:
        """Update UI when processing state changes."""
        prompt = self.query_one("#prompt", PromptInput)
        prompt.set_processing(self.is_processing)

    def _update_status_bar(self) -> None:
        """Update the status bar with current session info."""
        status = self.query_one("#status-bar", StatusBar)
        status.set_session_info(
            session_id=self.session_id,
            project_name=self.project_name,
            network_mode=self.network_mode,
        )

    # WebSocket Event Handlers

    def on_websocket_messages_event_received(
        self,
        message: WebSocketMessages.EventReceived,
    ) -> None:
        """Handle server events from WebSocket."""
        event = message.event

        # Ignore ping messages (keep-alive)
        if event.event_type == "ping":
            return

        if isinstance(event, SessionStartedEvent):
            self._handle_session_started(event)
        elif isinstance(event, AssistantMessageEvent):
            self._handle_assistant_message(event)
        elif isinstance(event, ToolStartEvent):
            self._handle_tool_start(event)
        elif isinstance(event, ToolOutputEvent):
            self._handle_tool_output(event)
        elif isinstance(event, ToolEndEvent):
            self._handle_tool_end(event)
        elif isinstance(event, DiffAppliedEvent):
            self._handle_diff_applied(event)
        elif isinstance(event, TestsFinishedEvent):
            self._handle_tests_finished(event)
        elif isinstance(event, ErrorEvent):
            self._handle_error(event)
        elif isinstance(event, DoneEvent):
            self._handle_done(event)

    def _handle_session_started(self, event: SessionStartedEvent) -> None:
        """Handle session started event."""
        self.session_id = event.session_id or ""
        self.network_mode = event.network_mode
        self.is_processing = False

        # Add welcome message
        self._add_message(
            f"Session started. Workspace: {event.workspace_path}",
            role="assistant",
        )

    def _handle_assistant_message(self, event: AssistantMessageEvent) -> None:
        """Handle assistant message event."""
        # Check if we should append to existing assistant message
        chat = self.query_one("#chat-container", VerticalScroll)
        messages = list(chat.query(ChatMessage))

        if messages and messages[-1].role == "assistant":
            # Append to last message
            messages[-1].content += event.content
        else:
            # Create new message
            self._add_message(event.content, role="assistant")

    def _handle_tool_start(self, event: ToolStartEvent) -> None:
        """Handle tool start event."""
        # Create a new tool block
        tool_block = ToolBlock(
            tool_name=event.tool,
            tool_input=event.tool_input,
        )

        # Track it by a unique key (session + tool)
        tool_key = f"{event.session_id}:{event.tool}"
        self._active_tools[tool_key] = tool_block

        # Track current tool for this session (for output events)
        if event.session_id:
            self._current_tool[event.session_id] = event.tool

        # Add to chat
        chat = self.query_one("#chat-container", VerticalScroll)
        chat.mount(tool_block)
        chat.scroll_end()

    def _handle_tool_output(self, event: ToolOutputEvent) -> None:
        """Handle tool output chunk."""
        # Get current tool for this session
        current_tool = ""
        if event.session_id:
            current_tool = self._current_tool.get(event.session_id, "")

        if current_tool and event.session_id:
            tool_key = f"{event.session_id}:{current_tool}"
            if tool_key in self._active_tools:
                tool_block = self._active_tools[tool_key]
                tool_block.append_output(event.chunk, event.stream)

    def _handle_tool_end(self, event: ToolEndEvent) -> None:
        """Handle tool completion."""
        tool_key = f"{event.session_id}:{event.tool}"
        if tool_key in self._active_tools:
            tool_block = self._active_tools[tool_key]
            tool_block.set_completed(event.exit_code)
            del self._active_tools[tool_key]

    def _handle_diff_applied(self, event: DiffAppliedEvent) -> None:
        """Handle diff applied event."""
        # Create a special diff tool block
        tool_block = ToolBlock(
            tool_name="apply_patch",
            tool_input={"files": event.files_changed},
        )
        tool_block.set_completed(0)
        tool_block.set_diff(event.files_changed, event.diff_preview)

        chat = self.query_one("#chat-container", VerticalScroll)
        chat.mount(tool_block)
        chat.scroll_end()

    def _handle_tests_finished(self, event: TestsFinishedEvent) -> None:
        """Handle tests finished event."""
        # Create a test result tool block
        tool_block = ToolBlock(
            tool_name="run_tests",
            tool_input={},
        )
        tool_block.set_completed(event.exit_code)
        tool_block.set_test_results(event.summary)

        chat = self.query_one("#chat-container", VerticalScroll)
        chat.mount(tool_block)
        chat.scroll_end()

    def _handle_error(self, event: ErrorEvent) -> None:
        """Handle error event."""
        self._add_message(f"Error: {event.message}", role="error")
        self.is_processing = False

    def _handle_done(self, event: DoneEvent) -> None:
        """Handle agent turn complete."""
        self.is_processing = False

    def _add_message(self, content: str, role: str = "assistant") -> None:
        """Add a chat message to the conversation."""
        chat = self.query_one("#chat-container", VerticalScroll)
        msg = ChatMessage(content=content, role=role)
        chat.mount(msg)
        chat.scroll_end()

    # Actions

    def action_cancel(self) -> None:
        """Cancel current agent turn."""
        if self.session_id and self.is_processing:
            # Send cancel message
            from client.tui.app import CognitionApp

            if isinstance(self.app, CognitionApp):
                self.run_worker(
                    self.app.ws_client.send(
                        {
                            "type": "cancel",
                            "session_id": self.session_id,
                        }
                    )
                )
            self.notify("Cancelling...", severity="information")

    def action_disconnect(self) -> None:
        """Disconnect from session and return to project picker."""
        if self.session_id:
            # Clear session state
            self.session_id = ""
            self.is_processing = False
            self._active_tools.clear()

        # Disconnect WebSocket (will trigger reconnection on new session)
        from client.tui.app import CognitionApp

        if isinstance(self.app, CognitionApp):
            self.run_worker(self.app.ws_client.disconnect(permanent=False))

        # Return to project picker
        self.app.pop_screen()

    def action_quit(self) -> None:
        """Quit the application."""
        self.app.exit()

    # Input handling

    def on_input_submitted(self, event: PromptInput.Submitted) -> None:
        """Handle user input submission."""
        value = event.value.strip()
        if not value:
            return

        # Clear input
        event.input.value = ""

        # Handle commands
        if value.startswith("/"):
            self._handle_command(value)
        else:
            self._handle_user_message(value)

    def _handle_command(self, command: str) -> None:
        """Process slash commands."""
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "/help":
            self._show_help()
        elif cmd == "/cancel":
            self.action_cancel()
        elif cmd == "/quit":
            self.action_quit()
        elif cmd == "/list":
            # Show project picker
            self.app.push_screen("project_picker")
        elif cmd == "/disconnect":
            self.action_disconnect()
        else:
            self._add_message(
                f"Unknown command: {cmd}. Type /help for available commands.", role="error"
            )

    def _handle_user_message(self, message: str) -> None:
        """Send user message to agent."""
        if not self.session_id:
            self._add_message(
                "No active session. Use /list to select a project or create a new one.",
                role="error",
            )
            return

        # Display user message
        self._add_message(message, role="user")

        # Mark as processing
        self.is_processing = True

        # Send via WebSocket
        from client.tui.app import CognitionApp

        if isinstance(self.app, CognitionApp):
            self.run_worker(
                self.app.ws_client.send(
                    {
                        "type": "user_msg",
                        "session_id": self.session_id,
                        "content": message,
                    }
                )
            )

    def _show_help(self) -> None:
        """Show help text."""
        help_text = """# Commands

**Chat Commands:**
- Type any message to send to the agent

**Slash Commands:**
- `/help` - Show this help message
- `/cancel` - Cancel current agent operation
- `/list` - Return to project list
- `/disconnect` - Disconnect from current session
- `/quit` - Exit the application

**Keyboard Shortcuts:**
- `Ctrl+C` - Cancel current operation
- `Ctrl+D` - Disconnect from session
- `Ctrl+Q` - Quit application
"""
        self._add_message(help_text, role="assistant")

    def set_project_info(self, project_id: str, project_name: str) -> None:
        """Set project information for this session."""
        self.project_name = project_name or project_id[:8]
        self._update_status_bar()
