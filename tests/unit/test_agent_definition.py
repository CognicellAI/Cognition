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

import pytest
import yaml

from server.app.agent.definition import (
    AgentConfig,
    AgentDefinition,
    SubagentDefinition,
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
        assert config.sandbox_profile is None
        assert config.sandbox_execution_role_arn is None

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

    def test_sandbox_config_fields(self):
        """Sandbox profile and role selectors are trusted agent config fields."""
        config = AgentConfig(
            sandbox_profile="tenant-runtime",
            sandbox_execution_role_arn=(
                "arn:aws:iam::123456789012:role/cognition-agent-runtime"
            ),
        )
        assert config.sandbox_profile == "tenant-runtime"
        assert (
            config.sandbox_execution_role_arn
            == "arn:aws:iam::123456789012:role/cognition-agent-runtime"
        )


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

    def test_with_config(self):
        """Test subagent with config."""
        subagent = SubagentDefinition(
            name="scanner",
            system_prompt="Focus on finding vulnerabilities...",
            config=AgentConfig(temperature=0.1, max_tokens=1000),
        )
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
            skills=[
                {
                    "name": "security",
                    "content": "---\nname: security\ndescription: Security review\n---\n",
                }
            ],
            memory=["AGENTS.md", "SECURITY.md"],
            subagents=[
                SubagentDefinition(
                    name="vulnerability-scanner",
                    system_prompt="Focus on finding vulnerabilities...",
                )
            ],
            interrupt_on={
                "execute": {"allowed_decisions": ["approve", "reject"]},
                "write_file": {"allowed_decisions": ["approve", "edit", "reject"]},
            },
            permissions=[
                {
                    "operations": ["read", "write"],
                    "paths": ["/workspace/repo/**"],
                    "mode": "allow",
                }
            ],
            middleware=["server.app.api.middleware.LoggingMiddleware"],
            config=AgentConfig(temperature=0.3, max_tokens=2000),
        )
        assert agent.name == "security-analyzer"
        assert len(agent.skills) == 1
        assert len(agent.memory) == 2
        assert len(agent.subagents) == 1
        assert agent.interrupt_on["execute"].allowed_decisions == ["approve", "reject"]
        assert agent.permissions[0].operations == ["read", "write"]
        assert agent.config.temperature == 0.3

    def test_to_subagent_includes_permissions(self):
        """Subagent specs preserve Deep Agents filesystem permission rules."""
        agent = AgentDefinition(
            name="docs-writer",
            system_prompt="Write docs only.",
            permissions=[
                {
                    "operations": ["write"],
                    "paths": ["/workspace/docs/**"],
                    "mode": "allow",
                }
            ],
        )

        subagent = agent.to_subagent()

        assert subagent["permissions"][0].operations == ["write"]
        assert subagent["permissions"][0].paths == ["/workspace/docs/**"]
        assert subagent["permissions"][0].mode == "allow"

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

    def test_to_subagent_does_not_construct_openai_compatible_model_string(self):
        """openai_compatible subagents must not emit provider-prefixed model strings."""
        agent = AgentDefinition(
            name="researcher",
            system_prompt="You are a researcher.",
            config=AgentConfig(
                provider="openai_compatible",
                model="google/gemini-3-flash-preview",
            ),
        )

        subagent = agent.to_subagent()

        assert subagent["model"] == "google/gemini-3-flash-preview"

    def test_valid_name_with_hyphen_and_underscore(self):
        """Test that names with hyphens and underscores are valid."""
        agent = AgentDefinition(
            name="test-agent_1",
            system_prompt="You are a test agent.",
        )
        assert agent.name == "test-agent_1"

    def test_tool_field_rejected(self):
        """Cognition-managed tool attachments are no longer supported."""
        with pytest.raises(ValueError):
            AgentDefinition(
                name="test-agent",
                system_prompt="You are a test agent.",
                tools=["my_custom_tool"],
            )

    def test_skill_directory_rejected(self):
        """Legacy source-directory skill attachments are rejected."""
        with pytest.raises(ValueError):
            AgentDefinition(
                name="test-agent",
                system_prompt="You are a test agent.",
                skills=[".cognition/skills/"],
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
            config=AgentConfig(temperature=0.5),
        )
        yaml_str = agent.to_yaml()
        assert "name: test-agent" in yaml_str
        assert "system_prompt: You are a test agent." in yaml_str
        assert "config:" in yaml_str
        assert "temperature: 0.5" in yaml_str

    def test_to_yaml_roundtrip(self):
        """Test that YAML export/import roundtrips correctly."""
        agent = AgentDefinition(
            name="test-agent",
            system_prompt="You are a test agent.",
            skills=[{"name": "test-skill", "content": "# Test skill"}],
            memory=["TEST.md"],
            config=AgentConfig(temperature=0.5, max_tokens=1000),
        )
        yaml_str = agent.to_yaml()
        data = yaml.safe_load(yaml_str)
        loaded = AgentDefinition.model_validate(data)
        assert loaded.name == agent.name
        assert loaded.system_prompt == agent.system_prompt
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
skills:
  - name: security
    content: |
      ---
      name: security
      description: Security review
      ---
memory:
  - AGENTS.md
  - SECURITY.md
interrupt_on:
  execute:
    allowed_decisions:
      - approve
      - reject
  write_file:
    allowed_decisions:
      - approve
      - edit
      - reject
config:
  temperature: 0.3
  max_tokens: 2000
""")
            temp_path = f.name

        try:
            agent = load_agent_definition(temp_path)
            assert agent.name == "security-analyzer"
            assert agent.system_prompt == "You are a security expert..."
            assert len(agent.skills) == 1
            assert len(agent.memory) == 2
            assert agent.interrupt_on["execute"].allowed_decisions == [
                "approve",
                "reject",
            ]
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


class TestAgentDefinitionPathValidation:
    """Tests for AgentDefinition path validation methods."""

    def test_validate_skill_paths(self):
        """Agent-owned bundles do not depend on host filesystem paths."""
        agent = AgentDefinition(
            name="test-agent",
            system_prompt="You are a test agent.",
            skills=[{"name": "review", "content": "# Review"}],
        )
        assert agent.validate_skill_paths() == []

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
            skills=[{"name": "clean-code", "content": "# Clean code"}],
            memory=["/fake/memory.md"],
        )
        results = agent.validate_all_paths()
        assert len(results["skills"]) == 0
        assert len(results["memory"]) == 1


def test_agent_skills_reject_legacy_registry_names() -> None:
    with pytest.raises(ValueError):
        AgentDefinition.model_validate(
            {
                "name": "legacy-skill-agent",
                "system_prompt": "Reject legacy skills.",
                "skills": ["registry-skill"],
            }
        )


def test_agent_skills_reject_duplicate_names_and_traversal() -> None:
    with pytest.raises(ValueError, match="unique"):
        AgentDefinition.model_validate(
            {
                "name": "duplicate-skills",
                "system_prompt": "Reject duplicate skills.",
                "skills": [
                    {"name": "review", "content": "# One"},
                    {"name": "review", "content": "# Two"},
                ],
            }
        )
    with pytest.raises(ValueError, match="safe relative POSIX"):
        AgentDefinition.model_validate(
            {
                "name": "traversal-skill",
                "system_prompt": "Reject traversal.",
                "skills": [
                    {
                        "name": "review",
                        "content": "# Review",
                        "files": {"../secret.txt": "no"},
                    }
                ],
            }
        )
