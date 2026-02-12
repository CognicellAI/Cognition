"""Project picker screen for selecting or creating projects."""

from typing import TYPE_CHECKING

from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

from client.tui.api import ApiClient, get_api_client
from client.tui.config import settings

if TYPE_CHECKING:
    from textual.app import ComposeResult


class ProjectPickerScreen(Screen):
    """Screen for selecting, creating, and managing projects."""

    CSS_PATH = "../styles/app.tcss"

    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("n", "new_project", "New Project"),
        ("q", "quit", "Quit"),
        ("enter", "select", "Select"),
    ]

    # Reactive state
    projects = reactive(list)
    is_loading = reactive(False)

    def __init__(self, api_client: ApiClient | None = None, **kwargs) -> None:
        """Initialize project picker.

        Args:
            api_client: Optional API client instance.
        """
        super().__init__(**kwargs)
        self.api = api_client
        self._selected_project_id: str | None = None

    def compose(self) -> "ComposeResult":
        """Compose the project picker layout."""
        yield Header(show_clock=True)

        with Container(id="project-picker-container"):
            yield Static("Projects", id="project-picker-header")

            # Project table
            table = DataTable(id="project-table")
            table.add_columns("Name", "Last Active", "Sessions", "Status")
            table.cursor_type = "row"
            yield table

            # Footer with hints
            with Horizontal(id="project-picker-footer"):
                yield Static("↑↓ to navigate, Enter to select, N for new, R to refresh, Q to quit")

        # New project dialog (hidden initially)
        with Container(id="new-project-dialog", classes="hidden"):
            yield Input(placeholder="Project name", id="new-project-name")
            with Horizontal():
                yield Button("Create", id="create-project-btn", variant="primary")
                yield Button("Cancel", id="cancel-create-btn")

        yield Footer()

    async def on_mount(self) -> None:
        """Load projects on mount."""
        await self._load_projects()

    async def _load_projects(self) -> None:
        """Load projects from API."""
        self.is_loading = True

        try:
            if not self.api:
                self.api = await get_api_client()

            # Check server health first
            try:
                await self.api.health()
            except Exception:
                self.notify("Server is not reachable", severity="error")
                return

            # Load resumable sessions
            result = await self.api.list_resumable()
            self.projects = result.get("sessions", [])

            self._update_table()

        except Exception as e:
            self.notify(f"Failed to load projects: {e}", severity="error")
        finally:
            self.is_loading = False

    def _update_table(self) -> None:
        """Update the project table with current data."""
        table = self.query_one("#project-table", DataTable)
        table.clear()

        for project in self.projects:
            project_id = project.get("project_id", "")
            name = project.get("user_prefix", project_id[:8])
            last_active = project.get("last_accessed", "Unknown")
            total_msgs = project.get("total_messages", 0)
            status = (
                "Pinned"
                if project.get("pinned")
                else f"{project.get('cleanup_in_days', '?')}d left"
            )

            # Format last active
            if isinstance(last_active, str) and "T" in last_active:
                # Truncate to date only for display
                last_active = last_active.split("T")[0]

            table.add_row(name, str(last_active), str(total_msgs), status, key=project_id)

        if not self.projects:
            table.add_row("(No projects)", "-", "-", "Create one with 'N'")

    # Actions

    def action_refresh(self) -> None:
        """Refresh project list."""
        self.run_worker(self._load_projects())

    def action_new_project(self) -> None:
        """Show new project dialog."""
        dialog = self.query_one("#new-project-dialog", Container)
        dialog.remove_class("hidden")
        self.query_one("#new-project-name", Input).focus()

    def action_select(self) -> None:
        """Select the highlighted project."""
        table = self.query_one("#project-table", DataTable)
        if table.cursor_row is not None:
            row_key = table.get_row_at(table.cursor_row)
            if row_key and len(row_key) > 0:
                # Get project ID from row key
                project_id = table.get_row_at(table.cursor_row)
                if project_id and len(project_id) > 0:
                    self.run_worker(self._start_session(str(project_id[0])))

    def action_quit(self) -> None:
        """Quit the application."""
        self.app.exit()

    # Event Handlers

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle project selection."""
        if event.row_key.value:
            self.run_worker(self._start_session(str(event.row_key.value)))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "create-project-btn":
            self.run_worker(self._create_new_project())
        elif event.button.id == "cancel-create-btn":
            self._hide_new_project_dialog()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission in new project dialog."""
        if event.input.id == "new-project-name":
            self.run_worker(self._create_new_project())

    # Helper methods

    async def _create_new_project(self) -> None:
        """Create a new project."""
        name_input = self.query_one("#new-project-name", Input)
        name = name_input.value.strip()

        if not name:
            self.notify("Please enter a project name", severity="warning")
            return

        try:
            if not self.api:
                self.api = await get_api_client()

            result = await self.api.create_project(
                user_prefix=name,
                network_mode=settings.default_network_mode,
            )

            project_id = result.get("project_id")
            if project_id:
                self.notify(f"Created project: {name}", severity="information")
                self._hide_new_project_dialog()
                await self._start_session(project_id, project_name=name)
            else:
                self.notify("Failed to create project", severity="error")

        except Exception as e:
            self.notify(f"Error creating project: {e}", severity="error")

    def _hide_new_project_dialog(self) -> None:
        """Hide the new project dialog."""
        dialog = self.query_one("#new-project-dialog", Container)
        dialog.add_class("hidden")
        name_input = self.query_one("#new-project-name", Input)
        name_input.value = ""
        self.query_one("#project-table", DataTable).focus()

    async def _start_session(self, project_id: str, project_name: str = "") -> None:
        """Start a session for the selected project.

        Args:
            project_id: The project ID to resume.
            project_name: Optional project name for display.
        """
        try:
            if not self.api:
                self.api = await get_api_client()

            self.notify(f"Starting session for {project_name or project_id[:8]}...")

            # Create session via API
            result = await self.api.create_session(
                project_id=project_id,
                network_mode=settings.default_network_mode,
            )

            session_id = result.get("session_id")
            if session_id:
                # Get project name if not provided
                if not project_name:
                    try:
                        project = await self.api.get_project(project_id)
                        project_name = project.get("user_prefix", project_id[:8])
                    except Exception:
                        project_name = project_id[:8]

                # Send create_session message via WebSocket
                from client.tui.app import CognitionApp

                if isinstance(self.app, CognitionApp):
                    await self.app.ws_client.send(
                        {
                            "type": "create_session",
                            "project_id": project_id,
                        }
                    )

                # Push session screen
                self.app.push_screen("session")

                # Set project info on the session screen
                session_screen = self.app.get_screen("session")
                if isinstance(session_screen, Screen):
                    from client.tui.screens.session import SessionScreen

                    if isinstance(session_screen, SessionScreen):
                        session_screen.set_project_info(project_id, project_name)

            else:
                self.notify("Failed to start session", severity="error")

        except Exception as e:
            self.notify(f"Error starting session: {e}", severity="error")
