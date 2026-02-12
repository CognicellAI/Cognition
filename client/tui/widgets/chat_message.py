"""Chat message widget with markdown rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Container
from textual.reactive import reactive
from textual.widgets import Markdown, Static

if TYPE_CHECKING:
    from textual.app import ComposeResult


class ChatMessage(Container):
    """Chat message bubble with markdown rendering.

    Displays user and assistant messages with appropriate styling.
    User messages shown with subtle styling, assistant messages with
    full markdown rendering.
    """

    role = reactive("assistant")
    content = reactive("")
    timestamp = reactive("")

    DEFAULT_CSS = """
    ChatMessage {
        width: 100%;
        height: auto;
        margin: 1 0;
    }
    
    .chat-message-user {
        background: $primary-darken-3;
        border-left: solid $primary;
        padding: 1;
    }
    
    .chat-message-assistant {
        background: $surface;
        border-left: solid $success;
        padding: 1;
    }
    
    .chat-message-error {
        background: $error-darken-3;
        border-left: solid $error;
        padding: 1;
    }
    
    .chat-message-role {
        color: $text-disabled;
        text-style: bold;
        margin-bottom: 1;
        height: 1;
    }
    
    Markdown {
        background: transparent;
        margin: 0;
        padding: 0;
    }
    """

    def __init__(
        self,
        content: str = "",
        role: str = "assistant",
        timestamp: str = "",
        *args,
        **kwargs,
    ) -> None:
        """Initialize chat message.

        Args:
            content: The message text content.
            role: "user", "assistant", or "error".
            timestamp: Optional timestamp string.
        """
        super().__init__(*args, **kwargs)
        self.role = role
        self.content = content
        self.timestamp = timestamp

    def compose(self) -> ComposeResult:
        """Compose the message layout."""
        # Role label
        role_text = self.role.upper()
        if self.timestamp:
            role_text += f" ({self.timestamp})"

        yield Static(role_text, classes="chat-message-role")

        # Content - Markdown for assistant, plain for user/error
        if self.role == "assistant":
            yield Markdown(self.content)
        else:
            yield Static(self.content)

    def on_mount(self) -> None:
        """Apply styling based on role."""
        self._apply_style()

    def _apply_style(self) -> None:
        """Apply the appropriate CSS class based on role."""
        self.remove_class("chat-message-user")
        self.remove_class("chat-message-assistant")
        self.remove_class("chat-message-error")

        if self.role == "user":
            self.add_class("chat-message-user")
        elif self.role == "error":
            self.add_class("chat-message-error")
        else:
            self.add_class("chat-message-assistant")

    def watch_content(self, content: str) -> None:
        """Update content when it changes."""
        # Only update if mounted (widgets exist)
        if not self.is_mounted:
            return

        # Find the content widget and update it
        try:
            if self.role == "assistant":
                md = self.query_one(Markdown)
                md.update(content)
            else:
                # For user/error, find the content Static (not the role label)
                statics = list(self.query(Static))
                if len(statics) > 1:
                    statics[1].update(content)  # Second Static is the content
        except Exception:
            # Widgets not ready yet, ignore
            pass

    @classmethod
    def user(cls, content: str, timestamp: str = "") -> "ChatMessage":
        """Create a user message."""
        return cls(content=content, role="user", timestamp=timestamp)

    @classmethod
    def assistant(cls, content: str, timestamp: str = "") -> "ChatMessage":
        """Create an assistant message."""
        return cls(content=content, role="assistant", timestamp=timestamp)

    @classmethod
    def error(cls, content: str, timestamp: str = "") -> "ChatMessage":
        """Create an error message."""
        return cls(content=content, role="error", timestamp=timestamp)
