"""Unit tests for config loader workspace and env-var handling."""

from __future__ import annotations

from pathlib import Path

from server.app.config_loader import _resolve_env_vars, load_config


class TestResolveEnvVars:
    def test_resolves_plain_env_var(self, monkeypatch) -> None:
        monkeypatch.setenv("HOME", "/tmp/home-dir")

        assert _resolve_env_vars("${HOME}") == "/tmp/home-dir"

    def test_resolves_default_when_env_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("MISSING_VALUE", raising=False)

        assert _resolve_env_vars("${MISSING_VALUE:-fallback}") == "fallback"

    def test_leaves_unresolved_placeholder_without_default(self, monkeypatch) -> None:
        monkeypatch.delenv("UNSET_VALUE", raising=False)

        assert _resolve_env_vars("${UNSET_VALUE}") == "${UNSET_VALUE}"

    def test_recurses_through_nested_dicts_and_lists(self, monkeypatch) -> None:
        monkeypatch.setenv("REGION", "us-east-1")
        monkeypatch.delenv("MODEL_ID", raising=False)

        config = {
            "llm": {
                "region": "${REGION}",
                "models": ["${MODEL_ID:-claude}", {"active": "${REGION}"}],
            }
        }

        assert _resolve_env_vars(config) == {
            "llm": {
                "region": "us-east-1",
                "models": ["claude", {"active": "us-east-1"}],
            }
        }

    def test_leaves_non_string_values_unchanged(self) -> None:
        config = {"count": 3, "enabled": True, "empty": None}

        assert _resolve_env_vars(config) == config


class TestLoadConfig:
    def test_load_config_uses_explicit_workspace_root(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        (workspace / ".cognition").mkdir(parents=True)
        (workspace / ".cognition" / "config.yaml").write_text(
            "llm:\n  provider: bedrock\n",
            encoding="utf-8",
        )

        app_dir = tmp_path / "app"
        app_dir.mkdir()

        config = load_config(cwd=workspace)
        missing = load_config(cwd=app_dir)

        assert config["llm"]["provider"] == "bedrock"
        assert missing == {}
