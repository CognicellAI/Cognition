"""Status bar widget showing session and connection info."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.reactive import reactive
from textual.widgets import Static

if TYPE_CHECKING:
    from textual.app import ComposeResult


class StatusBar(Static):
    """Status bar displaying session info and connection state."""

    # Reactive properties
    session_id = reactive("")
    project_name = reactive("No Project")
    network_mode = reactive("OFF")
    model = reactive("")
    is_connected = reactive(False)
    is_reconnecting = reactive(False)

    DEFAULT_CSS = """
    StatusBar {
        dock: top;
        height: 1;
        background: $primary-darken-2;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }
    
    .status-connected {
        color: $success;
    }
    
    .status-disconnected {
        color: $error;
    }
    
    .status-reconnecting {
        color: $warning;
    }
    
    .status-on {
        color: $warning;
    }
    """

    def compose(self) -> ComposeResult:
        """Compose the status bar layout."""
        yield Static(id="status-content")

    def watch_session_id(self) -> None:
        """Update display when session_id changes."""
        self._update_display()

    def watch_project_name(self) -> None:
        """Update display when project_name changes."""
        self._update_display()

    def watch_network_mode(self) -> None:
        """Update display when network_mode changes."""
        self._update_display()

    def watch_model(self) -> None:
        """Update display when model changes."""
        self._update_display()

    def watch_is_connected(self) -> None:
        """Update display when connection state changes."""
        self._update_display()

    def watch_is_reconnecting(self) -> None:
        """Update display when reconnecting state changes."""
        self._update_display()

    def _update_display(self) -> None:
        """Update the status bar text."""
        try:
            status = self.query_one("#status-content", Static)
        except Exception:
            # Widget not yet mounted
            return

        # Build connection indicator (plain text, no rich markup)
        if self.is_reconnecting:
            conn_indicator = "⟳ reconnecting"
            status.remove_class("status-connected", "status-disconnected")
            status.add_class("status-reconnecting")
        elif self.is_connected:
            conn_indicator = "● connected"
            status.remove_class("status-disconnected", "status-reconnecting")
            status.add_class("status-connected")
        else:
            conn_indicator = "● disconnected"
            status.remove_class("status-connected", "status-reconnecting")
            status.add_class("status-disconnected")

        # Build session info parts
        parts = [conn_indicator]

        if self.project_name and self.project_name != "No Project":
            parts.append(f"project: {self.project_name}")

        if self.session_id:
            short_id = self.session_id[:8]
            parts.append(f"session: {short_id}")

        if self.network_mode:
            if self.network_mode == "ON":
                parts.append(f"net: {self.network_mode}")
                status.add_class("status-on")
            else:
                parts.append(f"net: {self.network_mode}")

        if self.model:
            parts.append(f"model: {self.model}")

        # Update text without rich markup
        status.update(" | ".join(parts))

    def on_mount(self) -> None:
        """Initialize display on mount."""
        self._update_display()

    def set_session_info(
        self,
        session_id: str | None = None,
        project_name: str | None = None,
        network_mode: str | None = None,
        model: str | None = None,
    ) -> None:
        """Update all session info at once."""
        if session_id is not None:
            self.session_id = session_id
        if project_name is not None:
            self.project_name = project_name
        if network_mode is not None:
            self.network_mode = network_mode
        if model is not None:
            self.model = model

    def clear_session(self) -> None:
        """Clear all session info."""
        self.session_id = ""
        self.project_name = "No Project"
        self.network_mode = "OFF"
        self.model = ""

    def set_connection_state(self, connected: bool, reconnecting: bool = False) -> None:
        """Update connection state."""
        self.is_connected = connected
        self.is_reconnecting = reconnecting
