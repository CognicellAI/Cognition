"""Tests for PromptConfig model and prompt loading."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from server.app.models import PromptConfig


class TestPromptConfig:
    """Test PromptConfig model."""

    def test_default_values(self) -> None:
        """Test default PromptConfig values."""
        config = PromptConfig()

        assert config.type == "file"
        assert config.value == "system"

    def test_inline_prompt(self) -> None:
        """Test inline prompt type."""
        config = PromptConfig(type="inline", value="You are helpful")

        assert config.get_prompt_text() == "You are helpful"

    def test_file_prompt(self, tmp_path: Path) -> None:
        """Test file prompt type."""
        # Create prompt file
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        prompt_file = prompts_dir / "test.md"
        prompt_file.write_text("Test prompt content")

        config = PromptConfig(type="file", value="test")
        result = config.get_prompt_text(str(prompts_dir))

        assert result == "Test prompt content"

    def test_file_prompt_not_found(self, tmp_path: Path) -> None:
        """Test file prompt raises error when not found."""
        config = PromptConfig(type="file", value="nonexistent")

        with pytest.raises(FileNotFoundError):
            config.get_prompt_text(str(tmp_path))

    def test_file_prompt_without_md_extension(self, tmp_path: Path) -> None:
        """Test file prompt works with explicit path."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        prompt_file = prompts_dir / "custom.txt"
        prompt_file.write_text("Custom prompt")

        config = PromptConfig(type="file", value="custom.txt")
        result = config.get_prompt_text(str(prompts_dir))

        assert result == "Custom prompt"

    def test_mlflow_prompt_not_found(self) -> None:
        """Test MLflow prompt raises error when prompt not found."""
        config = PromptConfig(type="mlflow", value="nonexistent-prompt:v1")

        # MLflow is installed in test env, so this will fail with "not found"
        with pytest.raises(RuntimeError, match="Failed to load MLflow prompt"):
            config.get_prompt_text()

    def test_invalid_type_validation(self) -> None:
        """Test invalid prompt type is rejected by Pydantic validation."""
        # Pydantic validates the Literal type at creation time
        with pytest.raises(Exception):  # Pydantic validation error
            PromptConfig(type="invalid", value="test")  # type: ignore


class TestPromptConfigIntegration:
    """Integration tests for PromptConfig."""

    def test_settings_integration(self) -> None:
        """Test PromptConfig works with settings."""
        from server.app.settings import Settings

        # Create settings with default prompt config
        settings = Settings()

        # Should have default PromptConfig
        assert settings.system_prompt.type == "file"
        assert settings.system_prompt.value == "system"

    def test_settings_with_custom_prompt(self, tmp_path: Path) -> None:
        """Test settings with custom inline prompt."""
        from server.app.settings import Settings

        # Create settings with inline prompt
        settings = Settings(system_prompt=PromptConfig(type="inline", value="Custom system prompt"))

        assert settings.system_prompt.get_prompt_text() == "Custom system prompt"
