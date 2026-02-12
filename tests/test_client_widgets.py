"""Unit tests for client TUI widgets."""

import pytest
from unittest.mock import MagicMock, patch

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static, Markdown

from client.tui.widgets.status_bar import StatusBar
from client.tui.widgets.chat_message import ChatMessage
from client.tui.widgets.tool_block import ToolBlock
from client.tui.widgets.prompt import PromptInput


@pytest.mark.unit
class TestStatusBar:
    """Test StatusBar widget."""

    def test_initialization(self):
        """Test StatusBar initializes with default values."""
        bar = StatusBar()
        assert bar.session_id == ""
        assert bar.project_name == "No Project"
        assert bar.network_mode == "OFF"
        assert bar.model == ""
        assert bar.is_connected is False
        assert bar.is_reconnecting is False

    def test_set_session_info(self):
        """Test setting session info."""
        bar = StatusBar()
        bar.set_session_info(
            session_id="session-123",
            project_name="my-project",
            network_mode="ON",
            model="claude-3",
        )

        assert bar.session_id == "session-123"
        assert bar.project_name == "my-project"
        assert bar.network_mode == "ON"
        assert bar.model == "claude-3"

    def test_set_session_info_partial(self):
        """Test setting session info with partial data."""
        bar = StatusBar()
        bar.set_session_info(project_name="test-project")

        assert bar.project_name == "test-project"
        assert bar.session_id == ""  # Unchanged
        assert bar.network_mode == "OFF"  # Unchanged

    def test_set_connection_state(self):
        """Test setting connection state."""
        bar = StatusBar()

        bar.set_connection_state(connected=True, reconnecting=False)
        assert bar.is_connected is True
        assert bar.is_reconnecting is False

        bar.set_connection_state(connected=False, reconnecting=True)
        assert bar.is_connected is False
        assert bar.is_reconnecting is True

    def test_clear_session(self):
        """Test clearing session info."""
        bar = StatusBar()
        bar.set_session_info(
            session_id="session-123",
            project_name="my-project",
            network_mode="ON",
        )

        bar.clear_session()

        assert bar.session_id == ""
        assert bar.project_name == "No Project"
        assert bar.network_mode == "OFF"
        assert bar.model == ""

    def test_css_defined(self):
        """Test that CSS is defined."""
        bar = StatusBar()
        assert bar.DEFAULT_CSS is not None
        assert "StatusBar" in bar.DEFAULT_CSS


@pytest.mark.unit
class TestChatMessage:
    """Test ChatMessage widget."""

    def test_user_message_creation(self):
        """Test creating a user message."""
        msg = ChatMessage.user("Hello assistant", timestamp="10:30")

        assert msg.role == "user"
        assert msg.content == "Hello assistant"
        assert msg.timestamp == "10:30"

    def test_assistant_message_creation(self):
        """Test creating an assistant message."""
        msg = ChatMessage.assistant("I can help with that.", timestamp="10:31")

        assert msg.role == "assistant"
        assert msg.content == "I can help with that."
        assert msg.timestamp == "10:31"

    def test_error_message_creation(self):
        """Test creating an error message."""
        msg = ChatMessage.error("Error occurred", timestamp="10:32")

        assert msg.role == "error"
        assert msg.content == "Error occurred"
        assert msg.timestamp == "10:32"

    def test_message_initialization(self):
        """Test direct initialization."""
        msg = ChatMessage(
            content="Test content",
            role="user",
            timestamp="10:30",
        )

        assert msg.content == "Test content"
        assert msg.role == "user"
        assert msg.timestamp == "10:30"

    def test_css_defined(self):
        """Test that CSS is defined."""
        msg = ChatMessage()
        assert msg.DEFAULT_CSS is not None
        assert "ChatMessage" in msg.DEFAULT_CSS
        assert "chat-message-user" in msg.DEFAULT_CSS
        assert "chat-message-assistant" in msg.DEFAULT_CSS


@pytest.mark.unit
class TestToolBlock:
    """Test ToolBlock widget."""

    def test_initialization(self):
        """Test ToolBlock initializes correctly."""
        block = ToolBlock(tool_name="read_file")

        assert block.tool_name == "read_file"
        assert block.tool_input == {}

    def test_initialization_with_input(self):
        """Test ToolBlock with tool input."""
        tool_input = {"path": "src/main.py"}
        block = ToolBlock(tool_name="read_file", tool_input=tool_input)

        assert block.tool_input == tool_input

    def test_format_input(self):
        """Test formatting tool input."""
        block = ToolBlock(tool_name="test")
        tool_input = {"path": "file.py", "lines": 10}

        formatted = block._format_input(tool_input)

        assert "path" in formatted
        assert "file.py" in formatted
        assert "lines" in formatted

    def test_format_input_empty(self):
        """Test formatting empty tool input."""
        block = ToolBlock(tool_name="test")

        formatted = block._format_input({})

        assert formatted == "  (none)"

    def test_format_input_long_strings(self):
        """Test formatting with long strings."""
        block = ToolBlock(tool_name="test")
        long_string = "a" * 150
        tool_input = {"text": long_string}

        formatted = block._format_input(tool_input)

        # Long strings should be truncated with "..."
        assert "..." in formatted

    def test_tool_name_stored(self):
        """Test tool name is properly stored."""
        block = ToolBlock(tool_name="read_file")

        assert block.tool_name == "read_file"

    def test_css_defined(self):
        """Test that CSS is defined."""
        block = ToolBlock(tool_name="test")
        assert block.DEFAULT_CSS is not None
        assert "ToolBlock" in block.DEFAULT_CSS


@pytest.mark.unit
class TestPromptInput:
    """Test PromptInput widget."""

    def test_initialization(self):
        """Test PromptInput initializes correctly."""
        prompt = PromptInput()

        assert prompt.has_session is False
        assert prompt.is_processing is False

    def test_placeholder_no_session(self):
        """Test placeholder when no session exists."""
        prompt = PromptInput()

        expected = "Type /create <name> to start, or /list to see projects"
        assert prompt.placeholder == expected

    def test_placeholder_with_session(self):
        """Test placeholder with active session."""
        prompt = PromptInput()
        prompt.set_session_active(True)

        expected = "Send a message... (type /help for commands)"
        assert prompt.placeholder == expected

    def test_placeholder_processing(self):
        """Test placeholder while processing."""
        prompt = PromptInput()
        prompt.set_processing(True)

        assert prompt.placeholder == "Agent is thinking..."

    def test_set_session_active(self):
        """Test setting session active."""
        prompt = PromptInput()

        prompt.set_session_active(True)
        assert prompt.has_session is True

        prompt.set_session_active(False)
        assert prompt.has_session is False

    def test_set_processing(self):
        """Test setting processing state."""
        prompt = PromptInput()

        prompt.set_processing(True)
        assert prompt.is_processing is True
        assert prompt.disabled is True

        prompt.set_processing(False)
        assert prompt.is_processing is False
        assert prompt.disabled is False

    def test_css_defined(self):
        """Test that CSS is defined."""
        prompt = PromptInput()
        assert prompt.DEFAULT_CSS is not None
        assert "PromptInput" in prompt.DEFAULT_CSS
