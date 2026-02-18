"""Unit tests for agent_definition module.

Tests cover:
- Model validation
- YAML loading and saving
- Path validation
- Error handling
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from server.app.agent_definition import (
    AgentConfig,
    AgentDefinition,
    SubagentDefinition,
    create_default_agent_definition,
    load_agent_definition,
)


class TestAgentConfig:
    """Tests for AgentConfig model."""

    def test_default_values(self):
        """Test that default values are set correctly."""
        config = AgentConfig()
        assert config.temperature is None
        assert config.max_tokens is None
        assert config.provider is None
        assert config.model is None
        assert config.timeout_seconds is None

    def test_valid_temperature(self):
        """Test valid temperature values."""
        config = AgentConfig(temperature=0.5)
        assert config.temperature == 0.5

        config = AgentConfig(temperature=0.0)
        assert config.temperature == 0.0

        config = AgentConfig(temperature=2.0)
        assert config.temperature == 2.0

    def test_invalid_temperature(self):
        """Test that invalid temperature values raise errors."""
        with pytest.raises(ValueError):
            AgentConfig(temperature=-0.1)

        with pytest.raises(ValueError):
            AgentConfig(temperature=2.1)

    def test_valid_max_tokens(self):
        """Test valid max_tokens values."""
        config = AgentConfig(max_tokens=1000)
        assert config.max_tokens == 1000

    def test_invalid_max_tokens(self):
        """Test that invalid max_tokens values raise errors."""
        with pytest.raises(ValueError):
            AgentConfig(max_tokens=0)

        with pytest.raises(ValueError):
            AgentConfig(max_tokens=-1)

    def test_valid_timeout(self):
        """Test valid timeout values."""
        config = AgentConfig(timeout_seconds=30.0)
        assert config.timeout_seconds == 30.0

    def test_invalid_timeout(self):
        """Test that invalid timeout values raise errors."""
        with pytest.raises(ValueError):
            AgentConfig(timeout_seconds=0.0)

        with pytest.raises(ValueError):
            AgentConfig(timeout_seconds=-1.0)


class TestSubagentDefinition:
    """Tests for SubagentDefinition model."""

    def test_valid_subagent(self):
        """Test creating a valid subagent."""
        subagent = SubagentDefinition(
            name="test-subagent",
            system_prompt="You are a test subagent.",
        )
        assert subagent.name == "test-subagent"
        assert subagent.system_prompt == "You are a test subagent."
        assert subagent.tools == []
        assert subagent.config is None

    def test_empty_name(self):
        """Test that empty name raises error."""
        with pytest.raises(ValueError):
            SubagentDefinition(
                name="",
                system_prompt="You are a test subagent.",
            )

    def test_invalid_name_characters(self):
        """Test that invalid name characters raise error."""
        with pytest.raises(ValueError):
            SubagentDefinition(
                name="test subagent!",
                system_prompt="You are a test subagent.",
            )

    def test_with_tools_and_config(self):
        """Test subagent with tools and config."""
        subagent = SubagentDefinition(
            name="scanner",
            system_prompt="Focus on finding vulnerabilities...",
            tools=["server.app.tools.file_tools"],
            config=AgentConfig(temperature=0.1, max_tokens=1000),
        )
        assert len(subagent.tools) == 1
        assert subagent.config.temperature == 0.1


class TestAgentDefinition:
    """Tests for AgentDefinition model."""

    def test_minimal_valid_definition(self):
        """Test creating a minimal valid agent definition."""
        agent = AgentDefinition(
            name="test-agent",
            system_prompt="You are a test agent.",
        )
        assert agent.name == "test-agent"
        assert agent.system_prompt == "You are a test agent."
        assert agent.tools == []
        assert agent.skills == []
        assert agent.memory == []
        assert agent.subagents == []
        assert agent.interrupt_on == {}
        assert agent.middleware == []

    def test_full_definition(self):
        """Test creating a full agent definition."""
        agent = AgentDefinition(
            name="security-analyzer",
            system_prompt="You are a security expert...",
            tools=[
                "server.app.tools.file_tools",
                "server.app.tools.shell_tools",
            ],
            skills=[".cognition/skills/security"],
            memory=["AGENTS.md", "SECURITY.md"],
            subagents=[
                SubagentDefinition(
                    name="vulnerability-scanner",
                    system_prompt="Focus on finding vulnerabilities...",
                )
            ],
            interrupt_on={"execute": True, "write_file": False},
            middleware=["server.app.middleware.LoggingMiddleware"],
            config=AgentConfig(temperature=0.3, max_tokens=2000),
        )
        assert agent.name == "security-analyzer"
        assert len(agent.tools) == 2
        assert len(agent.skills) == 1
        assert len(agent.memory) == 2
        assert len(agent.subagents) == 1
        assert agent.interrupt_on["execute"] is True
        assert agent.config.temperature == 0.3

    def test_empty_name(self):
        """Test that empty name raises error."""
        with pytest.raises(ValueError):
            AgentDefinition(
                name="",
                system_prompt="You are a test agent.",
            )

    def test_empty_system_prompt(self):
        """Test that empty system_prompt raises error."""
        with pytest.raises(ValueError):
            AgentDefinition(
                name="test-agent",
                system_prompt="",
            )

    def test_invalid_name_characters(self):
        """Test that invalid name characters raise error."""
        with pytest.raises(ValueError):
            AgentDefinition(
                name="test agent!",
                system_prompt="You are a test agent.",
            )

    def test_valid_name_with_hyphen_and_underscore(self):
        """Test that names with hyphens and underscores are valid."""
        agent = AgentDefinition(
            name="test-agent_1",
            system_prompt="You are a test agent.",
        )
        assert agent.name == "test-agent_1"

    def test_invalid_tool_path(self):
        """Test that invalid tool paths raise error."""
        with pytest.raises(ValueError):
            AgentDefinition(
                name="test-agent",
                system_prompt="You are a test agent.",
                tools=["invalid_tool_path"],
            )

    def test_empty_tool_path(self):
        """Test that empty tool paths raise error."""
        with pytest.raises(ValueError):
            AgentDefinition(
                name="test-agent",
                system_prompt="You are a test agent.",
                tools=[""],
            )

    def test_empty_skill_path(self):
        """Test that empty skill paths raise error."""
        with pytest.raises(ValueError):
            AgentDefinition(
                name="test-agent",
                system_prompt="You are a test agent.",
                skills=[""],
            )

    def test_empty_memory_path(self):
        """Test that empty memory paths raise error."""
        with pytest.raises(ValueError):
            AgentDefinition(
                name="test-agent",
                system_prompt="You are a test agent.",
                memory=[""],
            )

    def test_empty_middleware_path(self):
        """Test that empty middleware paths raise error."""
        with pytest.raises(ValueError):
            AgentDefinition(
                name="test-agent",
                system_prompt="You are a test agent.",
                middleware=[""],
            )

    def test_to_yaml(self):
        """Test exporting to YAML."""
        agent = AgentDefinition(
            name="test-agent",
            system_prompt="You are a test agent.",
            tools=["server.app.tools.file_tools"],
            config=AgentConfig(temperature=0.5),
        )
        yaml_str = agent.to_yaml()
        assert "name: test-agent" in yaml_str
        assert "system_prompt: You are a test agent." in yaml_str
        assert "tools:" in yaml_str
        assert "config:" in yaml_str
        assert "temperature: 0.5" in yaml_str

    def test_to_yaml_roundtrip(self):
        """Test that YAML export/import roundtrips correctly."""
        agent = AgentDefinition(
            name="test-agent",
            system_prompt="You are a test agent.",
            tools=["server.app.tools.file_tools"],
            skills=[".cognition/skills/test"],
            memory=["TEST.md"],
            config=AgentConfig(temperature=0.5, max_tokens=1000),
        )
        yaml_str = agent.to_yaml()
        data = yaml.safe_load(yaml_str)
        loaded = AgentDefinition.model_validate(data)
        assert loaded.name == agent.name
        assert loaded.system_prompt == agent.system_prompt
        assert loaded.tools == agent.tools
        assert loaded.skills == agent.skills
        assert loaded.memory == agent.memory
        assert loaded.config.temperature == agent.config.temperature
        assert loaded.config.max_tokens == agent.config.max_tokens

    def test_save_to_file(self):
        """Test saving to file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            temp_path = f.name

        try:
            agent = AgentDefinition(
                name="test-agent",
                system_prompt="You are a test agent.",
            )
            agent.save_to_file(temp_path)

            with open(temp_path) as f:
                content = f.read()
            assert "name: test-agent" in content
        finally:
            os.unlink(temp_path)


class TestLoadAgentDefinition:
    """Tests for load_agent_definition function."""

    def test_load_valid_yaml(self):
        """Test loading a valid YAML file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("""
name: security-analyzer
system_prompt: "You are a security expert..."
tools:
  - server.app.tools.file_tools
  - server.app.tools.shell_tools
skills:
  - .cognition/skills/security
memory:
  - AGENTS.md
  - SECURITY.md
interrupt_on:
  execute: true
  write_file: false
config:
  temperature: 0.3
  max_tokens: 2000
""")
            temp_path = f.name

        try:
            agent = load_agent_definition(temp_path)
            assert agent.name == "security-analyzer"
            assert agent.system_prompt == "You are a security expert..."
            assert len(agent.tools) == 2
            assert len(agent.skills) == 1
            assert len(agent.memory) == 2
            assert agent.interrupt_on["execute"] is True
            assert agent.config.temperature == 0.3
        finally:
            os.unlink(temp_path)

    def test_load_with_subagents(self):
        """Test loading YAML with subagents."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("""
name: main-agent
system_prompt: "You are the main agent."
subagents:
  - name: sub-agent-1
    system_prompt: "You are subagent 1."
    tools:
      - server.app.tools.tool1
  - name: sub-agent-2
    system_prompt: "You are subagent 2."
""")
            temp_path = f.name

        try:
            agent = load_agent_definition(temp_path)
            assert len(agent.subagents) == 2
            assert agent.subagents[0].name == "sub-agent-1"
            assert agent.subagents[1].name == "sub-agent-2"
        finally:
            os.unlink(temp_path)

    def test_load_nonexistent_file(self):
        """Test that loading a nonexistent file raises error."""
        with pytest.raises(FileNotFoundError):
            load_agent_definition("/nonexistent/path/agent.yaml")

    def test_load_invalid_yaml(self):
        """Test that invalid YAML raises error."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("invalid: yaml: content: : :")
            temp_path = f.name

        try:
            with pytest.raises(ValueError):
                load_agent_definition(temp_path)
        finally:
            os.unlink(temp_path)

    def test_load_non_dict_yaml(self):
        """Test that non-dict YAML raises error."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("- just\n- a\n- list")
            temp_path = f.name

        try:
            with pytest.raises((ValueError, TypeError)):
                load_agent_definition(temp_path)
        finally:
            os.unlink(temp_path)

    def test_load_missing_required_fields(self):
        """Test that missing required fields raises error."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("name: test-agent\n")
            temp_path = f.name

        try:
            with pytest.raises((ValueError, TypeError)):
                load_agent_definition(temp_path)
        finally:
            os.unlink(temp_path)


class TestCreateDefaultAgentDefinition:
    """Tests for create_default_agent_definition function."""

    def test_default_creation(self):
        """Test creating a default agent definition."""
        agent = create_default_agent_definition()
        assert agent.name == "default-agent"
        assert "coding assistant" in agent.system_prompt.lower()
        assert ".cognition/skills/" in agent.skills
        assert "AGENTS.md" in agent.memory

    def test_custom_name(self):
        """Test creating with custom name."""
        agent = create_default_agent_definition("my-custom-agent")
        assert agent.name == "my-custom-agent"


class TestAgentDefinitionPathValidation:
    """Tests for AgentDefinition path validation methods."""

    def test_validate_tool_paths(self):
        """Test validating tool paths."""
        agent = AgentDefinition(
            name="test-agent",
            system_prompt="You are a test agent.",
            tools=[
                "server.app.agent_definition",  # exists
                "fake.module.that.does.not.exist",  # doesn't exist
            ],
        )
        failed = agent.validate_tool_paths()
        assert "fake.module.that.does.not.exist" in failed
        assert "server.app.agent_definition" not in failed

    def test_validate_skill_paths(self):
        """Test validating skill paths."""
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_dir = Path(temp_dir) / "skills"
            skill_dir.mkdir()

            agent = AgentDefinition(
                name="test-agent",
                system_prompt="You are a test agent.",
                skills=["skills"],
            )
            failed = agent.validate_skill_paths(temp_dir)
            assert len(failed) == 0

    def test_validate_memory_paths(self):
        """Test validating memory paths."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Test")
            temp_path = f.name

        try:
            agent = AgentDefinition(
                name="test-agent",
                system_prompt="You are a test agent.",
                memory=[temp_path],
            )
            failed = agent.validate_memory_paths()
            assert len(failed) == 0
        finally:
            os.unlink(temp_path)

    def test_validate_all_paths(self):
        """Test validating all paths at once."""
        agent = AgentDefinition(
            name="test-agent",
            system_prompt="You are a test agent.",
            tools=["fake.module"],
            skills=["/fake/skills"],
            memory=["/fake/memory.md"],
        )
        results = agent.validate_all_paths()
        assert len(results["tools"]) == 1
        assert len(results["skills"]) == 1
        assert len(results["memory"]) == 1
