"""Prompt input widget with slash command support."""

from textual.reactive import reactive
from textual.widgets import Input


class PromptInput(Input):
    """Input widget with placeholder that changes based on state.

    Provides slash command hints and disables input during agent processing.
    """

    # Reactive state properties
    has_session = reactive(False)
    is_processing = reactive(False)

    DEFAULT_CSS = """
    PromptInput {
        dock: bottom;
        height: 3;
        background: $surface;
        border-top: solid $primary;
        padding: 0 1;
    }
    
    PromptInput:focus {
        border-top: solid $success;
    }
    
    PromptInput.disabled {
        background: $surface-darken-2;
        color: $text-disabled;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        """Initialize prompt input."""
        super().__init__(*args, **kwargs)
        self._update_placeholder()

    def watch_has_session(self) -> None:
        """Update placeholder when session state changes."""
        self._update_placeholder()

    def watch_is_processing(self) -> None:
        """Disable/enable input when processing state changes."""
        self.disabled = self.is_processing
        self._update_placeholder()

    def _update_placeholder(self) -> None:
        """Update placeholder text based on current state."""
        if self.is_processing:
            self.placeholder = "Agent is thinking..."
        elif self.has_session:
            self.placeholder = "Send a message... (type /help for commands)"
        else:
            self.placeholder = "Type /create <name> to start, or /list to see projects"

    def set_session_active(self, active: bool) -> None:
        """Set whether a session is active."""
        self.has_session = active

    def set_processing(self, processing: bool) -> None:
        """Set whether the agent is currently processing."""
        self.is_processing = processing
