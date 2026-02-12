"""Unit tests for context_window, model_config, and usage_tracker modules."""

from __future__ import annotations

import pytest

from server.app.llm.context_window import (
    ContextBudget,
    compute_context_budget,
    estimate_messages_tokens,
    estimate_tokens,
    truncate_messages,
)
from server.app.llm.model_config import ModelConfig, ModelConfigManager
from server.app.llm.usage_tracker import UsageTracker


# --- Context Window Tests ---


class TestEstimateTokens:
    """Test token estimation."""

    def test_empty_string(self):
        """Test empty string returns zero."""
        result = estimate_tokens("")
        assert result.total_tokens == 0

    def test_short_text(self):
        """Test short text estimation."""
        result = estimate_tokens("Hello, world!")  # 13 chars
        assert result.total_tokens == 3  # ~13/4

    def test_long_text(self):
        """Test longer text estimation."""
        text = "x" * 4000  # 4000 chars
        result = estimate_tokens(text)
        assert result.total_tokens == 1000  # 4000/4

    def test_method_is_chars(self):
        """Test that method is 'chars' for estimate."""
        result = estimate_tokens("hello")
        assert result.method == "chars"


class TestEstimateMessagesTokens:
    """Test message-level token estimation."""

    def test_single_message(self):
        """Test single message estimation."""
        messages = [{"role": "user", "content": "Hello"}]
        tokens = estimate_messages_tokens(messages)
        # "Hello" = 5 chars / 4 = 1 token + 4 overhead = 5
        assert tokens >= 1

    def test_multiple_messages(self):
        """Test multiple messages."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        tokens = estimate_messages_tokens(messages)
        assert tokens > 0

    def test_empty_messages(self):
        """Test empty message list."""
        assert estimate_messages_tokens([]) == 0


class TestTruncateMessages:
    """Test message truncation."""

    def test_no_truncation_needed(self):
        """Test when messages fit within budget."""
        messages = [
            {"role": "user", "content": "Hi"},
        ]
        result = truncate_messages(messages, max_tokens=1000)
        assert len(result) == 1

    def test_truncation_keeps_recent(self):
        """Test that truncation keeps most recent messages."""
        messages = [
            {"role": "user", "content": "First message " * 100},
            {"role": "assistant", "content": "First reply " * 100},
            {"role": "user", "content": "Second message " * 100},
            {"role": "assistant", "content": "Second reply " * 100},
            {"role": "user", "content": "Latest question"},
        ]
        result = truncate_messages(messages, max_tokens=100)
        # Should keep latest messages
        assert len(result) < len(messages)
        assert result[-1]["content"] == "Latest question"

    def test_truncation_preserves_system(self):
        """Test that system message is preserved during truncation."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Long message " * 200},
            {"role": "assistant", "content": "Long reply " * 200},
            {"role": "user", "content": "Latest"},
        ]
        result = truncate_messages(messages, max_tokens=50)
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful."

    def test_empty_messages(self):
        """Test truncation of empty list."""
        result = truncate_messages([], max_tokens=100)
        assert result == []

    def test_summarize_strategy(self):
        """Test summarize_old strategy."""
        messages = [
            {"role": "user", "content": "First message " * 100},
            {"role": "assistant", "content": "First reply " * 100},
            {"role": "user", "content": "Latest"},
        ]
        result = truncate_messages(messages, max_tokens=50, strategy="summarize_old")
        assert len(result) >= 1


class TestContextBudget:
    """Test ContextBudget dataclass."""

    def test_available_input(self):
        """Test available input token calculation."""
        budget = ContextBudget(
            max_context=128000,
            max_output=16384,
            system_tokens=1000,
            history_tokens=5000,
            tool_tokens=2000,
        )
        expected = 128000 - 16384 - 1000 - 5000 - 2000
        assert budget.available_input == expected

    def test_total_used(self):
        """Test total used tokens."""
        budget = ContextBudget(
            max_context=128000,
            max_output=16384,
            system_tokens=1000,
            history_tokens=5000,
            tool_tokens=2000,
        )
        assert budget.total_used == 8000

    def test_utilization(self):
        """Test utilization percentage."""
        budget = ContextBudget(
            max_context=100000,
            max_output=10000,
            system_tokens=20000,
            history_tokens=30000,
        )
        assert budget.utilization == pytest.approx(0.5)

    def test_utilization_zero_context(self):
        """Test utilization with zero context."""
        budget = ContextBudget(max_context=0, max_output=0)
        assert budget.utilization == 0.0


class TestComputeContextBudget:
    """Test compute_context_budget function."""

    def test_basic_budget(self):
        """Test basic budget computation."""
        budget = compute_context_budget(
            system_prompt="You are helpful.",
            messages=[{"role": "user", "content": "Hi"}],
            max_context=128000,
            max_output=16384,
        )
        assert budget.max_context == 128000
        assert budget.max_output == 16384
        assert budget.system_tokens > 0
        assert budget.history_tokens > 0
        assert budget.available_input > 0


# --- Model Config Tests ---


class TestModelConfig:
    """Test ModelConfig dataclass."""

    def test_defaults(self):
        """Test default values are None."""
        config = ModelConfig()
        assert config.provider is None
        assert config.temperature is None
        assert config.max_tokens is None

    def test_merge_with_defaults(self):
        """Test merging session config with defaults."""
        defaults = ModelConfig(
            provider="openai",
            model="gpt-4o",
            temperature=0.7,
            max_tokens=4096,
        )
        session = ModelConfig(temperature=0.3)

        merged = session.merge_with_defaults(defaults)
        assert merged.provider == "openai"
        assert merged.model == "gpt-4o"
        assert merged.temperature == 0.3  # Overridden
        assert merged.max_tokens == 4096  # From defaults

    def test_merge_overrides_all(self):
        """Test that session values override all defaults."""
        defaults = ModelConfig(
            provider="openai",
            model="gpt-4o",
            temperature=0.7,
        )
        session = ModelConfig(
            provider="anthropic",
            model="claude-opus-4-6",
            temperature=0.0,
        )

        merged = session.merge_with_defaults(defaults)
        assert merged.provider == "anthropic"
        assert merged.model == "claude-opus-4-6"
        assert merged.temperature == 0.0

    def test_to_dict_excludes_none(self):
        """Test that to_dict excludes None values."""
        config = ModelConfig(temperature=0.5, max_tokens=1000)
        d = config.to_dict()
        assert "temperature" in d
        assert "max_tokens" in d
        assert "provider" not in d

    def test_from_dict(self):
        """Test creating config from dictionary."""
        config = ModelConfig.from_dict(
            {
                "temperature": 0.5,
                "max_tokens": 1000,
                "unknown_field": "ignored",
            }
        )
        assert config.temperature == 0.5
        assert config.max_tokens == 1000

    def test_system_prompt_append(self):
        """Test system prompt append."""
        config = ModelConfig(
            system_prompt="Base prompt.",
            system_prompt_append="Extra instructions.",
        )
        assert config.system_prompt == "Base prompt."
        assert config.system_prompt_append == "Extra instructions."


class TestModelConfigManager:
    """Test ModelConfigManager."""

    @pytest.fixture
    def manager(self):
        """Create a config manager."""
        return ModelConfigManager(
            default_config=ModelConfig(
                provider="openai",
                model="gpt-4o",
                temperature=0.7,
            )
        )

    def test_get_default(self, manager):
        """Test getting default config for unknown session."""
        config = manager.get_effective_config("unknown-session")
        assert config.provider == "openai"
        assert config.temperature == 0.7

    def test_set_session_config(self, manager):
        """Test setting session-specific config."""
        manager.set_session_config("sess-1", ModelConfig(temperature=0.0))
        config = manager.get_effective_config("sess-1")
        assert config.temperature == 0.0
        assert config.provider == "openai"  # From default

    def test_clear_session_config(self, manager):
        """Test clearing session config."""
        manager.set_session_config("sess-1", ModelConfig(temperature=0.0))
        manager.clear_session_config("sess-1")
        config = manager.get_effective_config("sess-1")
        assert config.temperature == 0.7  # Back to default

    def test_raw_session_config(self, manager):
        """Test getting raw session config (no merge)."""
        manager.set_session_config("sess-1", ModelConfig(temperature=0.0))
        raw = manager.get_session_config("sess-1")
        assert raw is not None
        assert raw.temperature == 0.0
        assert raw.provider is None  # Not merged

    def test_raw_session_config_missing(self, manager):
        """Test raw config for nonexistent session."""
        assert manager.get_session_config("nonexistent") is None


# --- Usage Tracker Tests ---


class TestUsageTracker:
    """Test UsageTracker."""

    @pytest.fixture
    def tracker(self):
        """Create a usage tracker."""
        return UsageTracker()

    def test_record_event(self, tracker):
        """Test recording a usage event."""
        event = tracker.record(
            session_id="sess-1",
            project_id="proj-1",
            input_tokens=1000,
            output_tokens=500,
        )
        assert event.input_tokens == 1000
        assert event.output_tokens == 500

    def test_session_summary(self, tracker):
        """Test session usage summary."""
        tracker.record(
            session_id="sess-1",
            project_id="proj-1",
            input_tokens=1000,
            output_tokens=500,
        )
        tracker.record(
            session_id="sess-1",
            project_id="proj-1",
            input_tokens=2000,
            output_tokens=1000,
        )

        summary = tracker.get_session_summary("sess-1")
        assert summary.total_input_tokens == 3000
        assert summary.total_output_tokens == 1500
        assert summary.event_count == 2

    def test_project_summary(self, tracker):
        """Test project-level usage summary."""
        tracker.record(
            session_id="sess-1",
            project_id="proj-1",
            input_tokens=1000,
            output_tokens=500,
        )
        tracker.record(
            session_id="sess-2",
            project_id="proj-1",
            input_tokens=2000,
            output_tokens=1000,
        )

        summary = tracker.get_project_summary("proj-1")
        assert summary.total_input_tokens == 3000
        assert summary.total_output_tokens == 1500
        assert summary.event_count == 2

    def test_empty_summary(self, tracker):
        """Test summary for nonexistent session."""
        summary = tracker.get_session_summary("nonexistent")
        assert summary.total_input_tokens == 0
        assert summary.event_count == 0

    def test_clear_session(self, tracker):
        """Test clearing session usage."""
        tracker.record(
            session_id="sess-1",
            project_id="proj-1",
            input_tokens=1000,
            output_tokens=500,
        )
        tracker.clear_session("sess-1")
        summary = tracker.get_session_summary("sess-1")
        assert summary.event_count == 0

    def test_total_tokens(self, tracker):
        """Test UsageSummary.total_tokens."""
        tracker.record(
            session_id="sess-1",
            project_id="proj-1",
            input_tokens=1000,
            output_tokens=500,
            reasoning_tokens=200,
        )
        summary = tracker.get_session_summary("sess-1")
        assert summary.total_tokens == 1700

    def test_get_session_events(self, tracker):
        """Test getting raw events for a session."""
        tracker.record(
            session_id="sess-1",
            project_id="proj-1",
            input_tokens=100,
            output_tokens=50,
        )
        events = tracker.get_session_events("sess-1")
        assert len(events) == 1
        assert events[0].input_tokens == 100
