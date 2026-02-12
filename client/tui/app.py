"""Textual TUI application for Cognition client."""

from pathlib import Path

from textual.app import App
from textual.command import CommandPalette
from textual.message import Message

from client.tui.api import ApiClient, api_client
from client.tui.screens import ProjectPickerScreen, SessionScreen
from client.tui.websocket import WebSocketClient, WebSocketMessages


class CognitionApp(App):
    """Main TUI application for Cognition client.

    Manages the WebSocket connection and provides screen navigation
    between the project picker and session chat.
    """

    # Use external CSS file
    CSS_PATH = Path(__file__).parent / "styles" / "app.tcss"

    # Global key bindings
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+p", "command_palette", "Command Palette"),
    ]

    # Screen registry
    SCREENS = {
        "project_picker": ProjectPickerScreen,
        "session": SessionScreen,
    }

    def __init__(self, api_client: ApiClient | None = None, **kwargs) -> None:
        """Initialize the application.

        Args:
            api_client: Optional API client instance to use.
        """
        super().__init__(**kwargs)
        self.ws_client = WebSocketClient(self)
        self.api = api_client

    async def on_mount(self) -> None:
        """Initialize application on mount."""
        # Initialize API client if not provided
        if not self.api:
            self.api = ApiClient()

        # Connect WebSocket
        try:
            await self.ws_client.connect()
            self.notify("Connected to server", severity="information", timeout=2)
        except ConnectionError as e:
            self.notify(f"Failed to connect: {e}", severity="error")

        # Push project picker as initial screen
        self.push_screen("project_picker")

    def on_websocket_messages_connected(self, message: WebSocketMessages.Connected) -> None:
        """Handle WebSocket connected event."""
        # Route to current screen if it can handle it
        if self.screen and hasattr(self.screen, "on_websocket_messages_connected"):
            self.screen.post_message(message)

    def on_websocket_messages_event_received(
        self, message: WebSocketMessages.EventReceived
    ) -> None:
        """Route WebSocket events to the active screen."""
        # Forward to current screen if it can handle it
        if self.screen and hasattr(self.screen, "on_websocket_messages_event_received"):
            self.screen.post_message(message)

    async def on_unmount(self) -> None:
        """Clean up on unmount."""
        # Disconnect WebSocket gracefully
        if self.ws_client.is_connected:
            await self.ws_client.disconnect(permanent=True)

        # Close API client
        if self.api:
            await self.api.close()

    def action_command_palette(self) -> None:
        """Show the command palette."""
        self.push_screen(CommandPalette())

    def _trigger_new_project(self) -> None:
        """Trigger new project creation.

        Switches to project picker and shows the new project dialog.
        """
        self.switch_screen("project_picker")
        screen = self.get_screen("project_picker")
        if isinstance(screen, ProjectPickerScreen):
            screen.action_new_project()


def main() -> None:
    """Run the TUI application."""
    app = CognitionApp()
    app.run()


if __name__ == "__main__":
    main()
