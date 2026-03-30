"""Tests for markdown frontmatter agent config parsing."""

from __future__ import annotations

from pathlib import Path

from server.app.agent.definition import load_agent_definition_from_markdown


def test_markdown_config_block_populates_agent_config(tmp_path: Path) -> None:
    path = tmp_path / "investigator.md"
    path.write_text(
        """---
description: Investigates incidents
temperature: 0.2
config:
  max_tokens: 16000
  recursion_limit: 500
  timeout_seconds: 45
---
You are an investigator.
""",
        encoding="utf-8",
    )

    definition = load_agent_definition_from_markdown(path)

    assert definition.config.temperature == 0.2
    assert definition.config.max_tokens == 16000
    assert definition.config.recursion_limit == 500
    assert definition.config.timeout_seconds == 45


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
