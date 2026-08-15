"""Tests for markdown frontmatter agent config parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.agent.definition import load_agent_definition_from_markdown


def test_markdown_config_block_populates_agent_config(tmp_path: Path) -> None:
    path = tmp_path / "investigator.md"
    path.write_text(
        """---
display_name: Incident Investigator
description: Investigates incidents
a2a:
  exposed: true
  public_interface_url: https://opaque.agents.example.com/a2a
  default_input_modes: [text/plain, application/json]
  default_output_modes: [application/json]
temperature: 0.2
config:
  max_tokens: 16000
  recursion_limit: 500
  timeout_seconds: 45
  sandbox_profile: lambda-default
  sandbox_execution_role_arn: arn:aws:iam::123456789012:role/cognition-agent
---
You are an investigator.
""",
        encoding="utf-8",
    )

    definition = load_agent_definition_from_markdown(path)

    assert definition.display_name == "Incident Investigator"
    assert definition.a2a.exposed is True
    assert definition.a2a.public_interface_url == "https://opaque.agents.example.com/a2a"
    assert definition.a2a.default_output_modes == ["application/json"]
    assert definition.config.temperature == 0.2
    assert definition.config.max_tokens == 16000
    assert definition.config.recursion_limit == 500
    assert definition.config.timeout_seconds == 45
    assert definition.config.sandbox_profile == "lambda-default"
    assert (
        definition.config.sandbox_execution_role_arn
        == "arn:aws:iam::123456789012:role/cognition-agent"
    )


def test_markdown_config_block_overrides_top_level_fields(tmp_path: Path) -> None:
    path = tmp_path / "reviewer.md"
    path.write_text(
        """---
temperature: 0.2
model: openai/gpt-4o-mini
config:
  temperature: 0.5
  model: claude-sonnet-4-6
  provider: bedrock
---
You are a reviewer.
""",
        encoding="utf-8",
    )

    definition = load_agent_definition_from_markdown(path)

    assert definition.config.temperature == 0.5
    assert definition.config.provider == "bedrock"
    assert definition.config.model == "claude-sonnet-4-6"


def test_markdown_rejects_removed_flat_a2a_fields(tmp_path: Path) -> None:
    path = tmp_path / "legacy.md"
    path.write_text(
        """---
a2a_exposed: true
---
Legacy definition.
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Use nested 'a2a' configuration"):
        load_agent_definition_from_markdown(path)
