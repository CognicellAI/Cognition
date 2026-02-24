"""Unit tests for AgentDefinitionRegistry (P3 Multi-Agent Registry).

Tests for the agent definition registry that manages built-in and user-defined agents,
including YAML/Markdown loading, mode filtering, and Deep Agents TypedDict translation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.agent.agent_definition_registry import (
    AgentDefinitionRegistry,
    get_agent_definition_registry,
    initialize_agent_definition_registry,
    set_agent_definition_registry,
)
from server.app.agent.definition import AgentDefinition


class TestAgentDefinitionRegistryBuiltins:
    """Test built-in agents are always present."""

    def test_default_agent_present(self):
        """Registry always contains 'default' agent."""
        registry = AgentDefinitionRegistry()

        agent = registry.get("default")

        assert agent is not None
        assert agent.name == "default"
        assert agent.native is True
        assert agent.mode in ("primary", "all")

    def test_readonly_agent_present(self):
        """Registry always contains 'readonly' agent."""
        registry = AgentDefinitionRegistry()

        agent = registry.get("readonly")

        assert agent is not None
        assert agent.name == "readonly"
        assert agent.native is True
        assert agent.mode in ("primary", "all")

    def test_builtin_agents_not_hidden(self):
        """Built-in agents are not hidden."""
        registry = AgentDefinitionRegistry()

        default = registry.get("default")
        readonly = registry.get("readonly")

        assert default.hidden is False
        assert readonly.hidden is False

    def test_builtin_agents_are_primary_mode(self):
        """Built-in agents have primary mode and appear in primaries()."""
        registry = AgentDefinitionRegistry()

        primaries = registry.primaries()
        primary_names = [a.name for a in primaries]

        assert "default" in primary_names
        assert "readonly" in primary_names

    def test_readonly_has_interrupt_on_for_write_tools(self):
        """Readonly agent has interrupt_on for write/execute tools."""
        registry = AgentDefinitionRegistry()

        readonly = registry.get("readonly")

        # Should interrupt on tools that modify state
        assert "write_file" in readonly.interrupt_on
        assert "edit_file" in readonly.interrupt_on
        assert "execute" in readonly.interrupt_on

    def test_builtin_agents_have_descriptions(self):
        """Built-in agents have non-empty descriptions."""
        registry = AgentDefinitionRegistry()

        default = registry.get("default")
        readonly = registry.get("readonly")

        assert default.description is not None
        assert len(default.description) > 0
        assert readonly.description is not None
        assert len(readonly.description) > 0

    def test_list_returns_both_builtins(self):
        """List returns both built-in agents."""
        registry = AgentDefinitionRegistry()

        agents = registry.list()
        names = [a.name for a in agents]

        assert "default" in names
        assert "readonly" in names


class TestAgentDefinitionRegistryUserAgents:
    """Test user-defined agent loading from workspace."""

    def test_load_yaml_agent(self, tmp_path: Path):
        """Load user agent from YAML file."""
        # Create .cognition/agents directory
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        # Write YAML agent definition
        yaml_content = """name: test-agent
system_prompt: You are a test agent.
description: A test agent for unit testing
mode: primary
"""
        (agents_dir / "test-agent.yaml").write_text(yaml_content)

        # Initialize registry
        registry = AgentDefinitionRegistry(tmp_path)

        # Verify agent loaded
        agent = registry.get("test-agent")
        assert agent is not None
        assert agent.name == "test-agent"
        assert agent.system_prompt == "You are a test agent."
        assert agent.description == "A test agent for unit testing"
        assert agent.mode == "primary"
        assert agent.native is False  # User-defined

    def test_load_markdown_agent(self, tmp_path: Path):
        """Load user agent from Markdown file with frontmatter."""
        # Create .cognition/agents directory
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        # Write Markdown agent definition
        md_content = """---
description: A markdown test agent
mode: subagent
model: anthropic/claude-haiku
---
You are a markdown test agent. Your job is to test things.
"""
        (agents_dir / "md-test.md").write_text(md_content)

        # Initialize registry
        registry = AgentDefinitionRegistry(tmp_path)

        # Verify agent loaded
        agent = registry.get("md-test")
        assert agent is not None
        assert agent.name == "md-test"
        assert "Your job is to test things" in agent.system_prompt
        assert agent.description == "A markdown test agent"
        assert agent.mode == "subagent"
        assert agent.native is False

    def test_markdown_agent_filename_becomes_name(self, tmp_path: Path):
        """Markdown filename stem becomes agent name."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        (agents_dir / "my-custom-agent.md").write_text(
            "---\ndescription: Custom agent\n---\nYou are an agent."
        )

        registry = AgentDefinitionRegistry(tmp_path)

        assert registry.get("my-custom-agent") is not None
        assert registry.get("my-custom-agent").name == "my-custom-agent"

    def test_markdown_agent_body_becomes_system_prompt(self, tmp_path: Path):
        """Markdown body content becomes system prompt."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        body = "You are a specialized agent.\n\nYou do X, Y, and Z."
        (agents_dir / "body-test.md").write_text(f"---\ndescription: Body test\n---\n{body}")

        registry = AgentDefinitionRegistry(tmp_path)
        agent = registry.get("body-test")

        assert agent.system_prompt == body

    def test_user_agent_overrides_builtin_by_name(self, tmp_path: Path):
        """User agent with same name as built-in overrides it."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        # Override default with custom system prompt
        custom_prompt = "You are a CUSTOM default agent."
        (agents_dir / "default.yaml").write_text(
            f'name: default\nsystem_prompt: "{custom_prompt}"\n'
        )

        registry = AgentDefinitionRegistry(tmp_path)
        default = registry.get("default")

        assert default.system_prompt == custom_prompt
        assert default.native is False  # Now user-defined

    def test_invalid_yaml_skipped_not_fatal(self, tmp_path: Path):
        """Invalid YAML files are skipped without crashing registry."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        # Write invalid YAML
        (agents_dir / "invalid.yaml").write_text("not valid: yaml: content :{{")

        # Registry should still initialize
        registry = AgentDefinitionRegistry(tmp_path)

        # Built-ins should be present
        assert registry.get("default") is not None
        assert registry.get("readonly") is not None

        # Invalid agent should not be loaded
        assert registry.get("invalid") is None

    def test_missing_agents_dir_is_ok(self, tmp_path: Path):
        """Workspace without .cognition/agents/ still initializes."""
        # No agents directory
        registry = AgentDefinitionRegistry(tmp_path)

        # Built-ins should be present
        assert len(registry.list()) == 2
        assert registry.get("default") is not None
        assert registry.get("readonly") is not None

    def test_multiple_user_agents_loaded(self, tmp_path: Path):
        """Multiple user agents are loaded from different files."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        # Create multiple agents
        for i in range(3):
            (agents_dir / f"agent-{i}.yaml").write_text(
                f'name: agent-{i}\nsystem_prompt: "Agent {i}"\n'
            )

        registry = AgentDefinitionRegistry(tmp_path)

        # Should have 2 built-ins + 3 user agents
        assert len(registry.list()) == 5
        for i in range(3):
            assert registry.get(f"agent-{i}") is not None


class TestAgentDefinitionRegistryLookup:
    """Test agent lookup and filtering."""

    def test_get_existing_agent(self):
        """Get existing agent by name."""
        registry = AgentDefinitionRegistry()

        agent = registry.get("default")

        assert agent is not None
        assert agent.name == "default"

    def test_get_nonexistent_agent_returns_none(self):
        """Get non-existent agent returns None."""
        registry = AgentDefinitionRegistry()

        agent = registry.get("no-such-agent")

        assert agent is None

    def test_list_excludes_hidden_by_default(self, tmp_path: Path):
        """List excludes hidden agents by default."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        (agents_dir / "hidden.yaml").write_text("name: hidden\nsystem_prompt: x\nhidden: true\n")
        (agents_dir / "visible.yaml").write_text("name: visible\nsystem_prompt: x\nhidden: false\n")

        registry = AgentDefinitionRegistry(tmp_path)

        visible_list = registry.list(include_hidden=False)
        full_list = registry.list(include_hidden=True)

        assert len(visible_list) == 3  # 2 built-ins + visible
        assert len(full_list) == 4  # 2 built-ins + visible + hidden

        visible_names = [a.name for a in visible_list]
        assert "hidden" not in visible_names
        assert "visible" in visible_names

    def test_list_includes_hidden_when_requested(self, tmp_path: Path):
        """List includes hidden agents when include_hidden=True."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        (agents_dir / "hidden.yaml").write_text("name: hidden\nsystem_prompt: x\nhidden: true\n")

        registry = AgentDefinitionRegistry(tmp_path)

        full_list = registry.list(include_hidden=True)
        names = [a.name for a in full_list]

        assert "hidden" in names

    def test_primaries_includes_primary_mode(self, tmp_path: Path):
        """primaries() includes agents with mode='primary'."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        (agents_dir / "primary.yaml").write_text("name: primary\nsystem_prompt: x\nmode: primary\n")

        registry = AgentDefinitionRegistry(tmp_path)

        primaries = registry.primaries()
        names = [a.name for a in primaries]

        assert "primary" in names

    def test_primaries_includes_all_mode(self, tmp_path: Path):
        """primaries() includes agents with mode='all'."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        (agents_dir / "all-mode.yaml").write_text("name: all-mode\nsystem_prompt: x\nmode: all\n")

        registry = AgentDefinitionRegistry(tmp_path)

        primaries = registry.primaries()
        names = [a.name for a in primaries]

        assert "all-mode" in names

    def test_primaries_excludes_subagent_mode(self, tmp_path: Path):
        """primaries() excludes agents with mode='subagent'."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        (agents_dir / "subagent.yaml").write_text(
            "name: subagent\nsystem_prompt: x\nmode: subagent\n"
        )

        registry = AgentDefinitionRegistry(tmp_path)

        primaries = registry.primaries()
        names = [a.name for a in primaries]

        assert "subagent" not in names

    def test_subagents_includes_subagent_mode(self, tmp_path: Path):
        """subagents() includes agents with mode='subagent'."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        (agents_dir / "subagent.yaml").write_text(
            "name: subagent\nsystem_prompt: x\nmode: subagent\n"
        )

        registry = AgentDefinitionRegistry(tmp_path)

        subagents = registry.subagents()
        names = [a.name for a in subagents]

        assert "subagent" in names

    def test_subagents_includes_all_mode(self, tmp_path: Path):
        """subagents() includes agents with mode='all'."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        (agents_dir / "all-mode.yaml").write_text("name: all-mode\nsystem_prompt: x\nmode: all\n")

        registry = AgentDefinitionRegistry(tmp_path)

        subagents = registry.subagents()
        names = [a.name for a in subagents]

        assert "all-mode" in names

    def test_subagents_excludes_primary_mode(self, tmp_path: Path):
        """subagents() excludes agents with mode='primary'."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        (agents_dir / "primary.yaml").write_text(
            "name: primary-only\nsystem_prompt: x\nmode: primary\n"
        )

        registry = AgentDefinitionRegistry(tmp_path)

        subagents = registry.subagents()
        names = [a.name for a in subagents]

        assert "primary-only" not in names

    def test_all_mode_in_both_primaries_and_subagents(self, tmp_path: Path):
        """Agent with mode='all' appears in both lists."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        (agents_dir / "universal.yaml").write_text("name: universal\nsystem_prompt: x\nmode: all\n")

        registry = AgentDefinitionRegistry(tmp_path)

        primary_names = [a.name for a in registry.primaries()]
        subagent_names = [a.name for a in registry.subagents()]

        assert "universal" in primary_names
        assert "universal" in subagent_names


class TestAgentDefinitionRegistryValidation:
    """Test agent validation for primary selection."""

    def test_is_valid_primary_existing_primary(self):
        """Existing primary agent is valid."""
        registry = AgentDefinitionRegistry()

        assert registry.is_valid_primary("default") is True
        assert registry.is_valid_primary("readonly") is True

    def test_is_valid_primary_unknown_returns_false(self):
        """Unknown agent name is not valid."""
        registry = AgentDefinitionRegistry()

        assert registry.is_valid_primary("no-such-agent") is False

    def test_is_valid_primary_subagent_mode_returns_false(self, tmp_path: Path):
        """Subagent mode agent is not valid for primary selection."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        (agents_dir / "helper.yaml").write_text("name: helper\nsystem_prompt: x\nmode: subagent\n")

        registry = AgentDefinitionRegistry(tmp_path)

        assert registry.is_valid_primary("helper") is False

    def test_is_valid_primary_hidden_returns_false(self, tmp_path: Path):
        """Hidden agent is not valid for primary selection."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        (agents_dir / "hidden.yaml").write_text(
            "name: hidden\nsystem_prompt: x\nmode: primary\nhidden: true\n"
        )

        registry = AgentDefinitionRegistry(tmp_path)

        assert registry.is_valid_primary("hidden") is False

    def test_is_valid_primary_all_mode_returns_true(self, tmp_path: Path):
        """All mode agent is valid for primary selection."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        (agents_dir / "flexible.yaml").write_text("name: flexible\nsystem_prompt: x\nmode: all\n")

        registry = AgentDefinitionRegistry(tmp_path)

        assert registry.is_valid_primary("flexible") is True


class TestAgentDefinitionRegistryReload:
    """Test hot-reload functionality."""

    def test_reload_picks_up_new_file(self, tmp_path: Path):
        """Reload discovers newly added agent files."""
        registry = AgentDefinitionRegistry(tmp_path)

        # Initially no user agents
        assert len(registry.list()) == 2
        assert registry.get("new-agent") is None

        # Add new file
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "new-agent.yaml").write_text("name: new-agent\nsystem_prompt: New!\n")

        # Reload
        registry.reload()

        # New agent should now be present
        assert registry.get("new-agent") is not None
        assert registry.get("new-agent").system_prompt == "New!"

    def test_reload_removes_deleted_file(self, tmp_path: Path):
        """Reload removes agents from deleted files."""
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)

        # Create agent
        (agents_dir / "temp.yaml").write_text("name: temp\nsystem_prompt: Temp\n")

        registry = AgentDefinitionRegistry(tmp_path)
        assert registry.get("temp") is not None

        # Delete file
        (agents_dir / "temp.yaml").unlink()

        # Reload
        registry.reload()

        # Agent should be gone
        assert registry.get("temp") is None

    def test_reload_preserves_builtins_always(self, tmp_path: Path):
        """Reload always restores built-in agents."""
        registry = AgentDefinitionRegistry(tmp_path)

        # Remove built-ins from internal dict (simulating corruption)
        del registry._agents["default"]
        del registry._agents["readonly"]

        assert registry.get("default") is None

        # Reload
        registry.reload()

        # Built-ins should be back
        assert registry.get("default") is not None
        assert registry.get("readonly") is not None


class TestAgentDefinitionToSubagent:
    """Test conversion to Deep Agents SubAgent TypedDict."""

    def test_to_subagent_required_fields_present(self):
        """to_subagent() always includes name, description, system_prompt."""
        agent = AgentDefinition(
            name="test",
            system_prompt="You are a test.",
            description="Test agent",
        )

        spec = agent.to_subagent()

        assert spec["name"] == "test"
        assert spec["description"] == "Test agent"
        assert spec["system_prompt"] == "You are a test."

    def test_to_subagent_model_no_provider(self):
        """Model without provider is passed as-is."""
        agent = AgentDefinition(
            name="test",
            system_prompt="x",
        )
        agent.config.model = "gpt-4o"

        spec = agent.to_subagent()

        assert spec["model"] == "gpt-4o"

    def test_to_subagent_model_with_provider_concatenated(self):
        """Provider and model are concatenated with colon."""
        agent = AgentDefinition(
            name="test",
            system_prompt="x",
        )
        agent.config.provider = "anthropic"
        agent.config.model = "claude-haiku"

        spec = agent.to_subagent()

        assert spec["model"] == "anthropic:claude-haiku"

    def test_to_subagent_none_description_becomes_empty_string(self):
        """None description becomes empty string."""
        agent = AgentDefinition(
            name="test",
            system_prompt="x",
            description=None,
        )

        spec = agent.to_subagent()

        assert spec["description"] == ""

    def test_to_subagent_skills_included_when_set(self):
        """Skills list is included when set."""
        agent = AgentDefinition(
            name="test",
            system_prompt="x",
            skills=["skill1", "skill2"],
        )

        spec = agent.to_subagent()

        assert spec["skills"] == ["skill1", "skill2"]

    def test_to_subagent_empty_tools_omitted(self):
        """Empty tools list is omitted from spec."""
        agent = AgentDefinition(
            name="test",
            system_prompt="x",
            tools=[],
        )

        spec = agent.to_subagent()

        assert "tools" not in spec

    def test_to_subagent_empty_skills_omitted(self):
        """Empty skills list is omitted from spec."""
        agent = AgentDefinition(
            name="test",
            system_prompt="x",
            skills=[],
        )

        spec = agent.to_subagent()

        assert "skills" not in spec

    def test_to_subagent_interrupt_on_included_when_set(self):
        """interrupt_on is included when set."""
        agent = AgentDefinition(
            name="test",
            system_prompt="x",
            interrupt_on={"write_file": True, "edit_file": True},
        )

        spec = agent.to_subagent()

        assert spec["interrupt_on"] == {"write_file": True, "edit_file": True}


class TestAgentDefinitionRegistryGlobal:
    """Test global registry functions."""

    def test_get_agent_registry_before_init(self):
        """Get registry before initialization returns None."""
        # Ensure no global registry
        from server.app.agent.agent_definition_registry import _registry

        global _registry
        _registry = None

        assert get_agent_definition_registry() is None

    def test_set_agent_registry(self):
        """Set global agent registry."""
        registry = AgentDefinitionRegistry()

        set_agent_definition_registry(registry)

        assert get_agent_definition_registry() is registry

    def test_initialize_agent_registry(self, tmp_path: Path):
        """Initialize global agent registry."""
        # Clear any existing registry
        set_agent_definition_registry(None)

        registry = initialize_agent_definition_registry(tmp_path)

        assert registry is not None
        assert get_agent_definition_registry() is registry
        # Should have built-ins
        assert registry.get("default") is not None
        assert registry.get("readonly") is not None
