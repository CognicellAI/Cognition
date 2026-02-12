"""Unit tests for client TUI screens."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from client.tui.screens.project_picker import ProjectPickerScreen
from client.tui.screens.session import SessionScreen


@pytest.mark.unit
class TestProjectPickerScreen:
    """Test ProjectPickerScreen."""

    @pytest.fixture
    def mock_app(self):
        """Mock Textual App."""
        app = MagicMock()
        app.push_screen = MagicMock()
        app.pop_screen = MagicMock()
        app.notify = MagicMock()
        app.run_worker = MagicMock()
        return app

    def test_initialization(self, mock_app):
        """Test ProjectPickerScreen initializes."""
        screen = ProjectPickerScreen()
        assert screen is not None

    def test_css_defined(self, mock_app):
        """Test that CSS is defined."""
        screen = ProjectPickerScreen()
        assert screen.DEFAULT_CSS is not None


@pytest.mark.unit
class TestSessionScreen:
    """Test SessionScreen."""

    @pytest.fixture
    def mock_app(self):
        """Mock Textual App."""
        app = MagicMock()
        app.post_message = MagicMock()
        app.notify = MagicMock()
        app.push_screen = MagicMock()
        app.pop_screen = MagicMock()
        app.exit = MagicMock()
        return app

    def test_initialization(self, mock_app):
        """Test SessionScreen initializes."""
        screen = SessionScreen()
        assert screen is not None

    def test_css_defined(self, mock_app):
        """Test that CSS is defined."""
        screen = SessionScreen()
        assert screen.DEFAULT_CSS is not None

    def test_keyboard_bindings(self):
        """Test that keyboard bindings are defined."""
        screen = SessionScreen()
        assert hasattr(screen, "BINDINGS")
        assert len(screen.BINDINGS) > 0
