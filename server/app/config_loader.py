"""YAML configuration loader.

Supports hierarchical configuration:
1. Built-in defaults
2. ~/.cognition/config.yaml (global user prefs)
3. .cognition/config.yaml (project-level)
4. Environment variables / .env (highest precedence)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from server.app.settings import Settings


def get_global_config_path() -> Path:
    """Get path to global config file."""
    home = Path.home()
    config_dir = home / ".cognition"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "config.yaml"


def get_project_config_path(cwd: Path | None = None) -> Path | None:
    """Get path to project config file if it exists.

    Searches from cwd up to root for .cognition/config.yaml
    """
    if cwd is None:
        cwd = Path.cwd()

    current = cwd.resolve()

    while current != current.parent:
        config_path = current / ".cognition" / "config.yaml"
        if config_path.exists():
            return config_path
        current = current.parent

    return None


def load_yaml_file(path: Path) -> dict[str, Any]:
    """Load YAML file and return dict."""
    if not HAS_YAML:
        raise ImportError(
            "PyYAML is required for YAML config support. Install with: uv pip install pyyaml"
        )

    if not path.exists():
        return {}

    try:
        with open(path, "r") as f:
            content = yaml.safe_load(f)
            return content if isinstance(content, dict) else {}
    except Exception as e:
        print(f"Warning: Failed to load config from {path}: {e}")
        return {}


def deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dictionaries.

    Override values take precedence.
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def load_config(cwd: Path | None = None) -> dict[str, Any]:
    """Load configuration from all sources.

    Loads and merges:
    1. Global config (~/.cognition/config.yaml)
    2. Project config (.cognition/config.yaml)

    Returns merged configuration dict.
    """
    config: dict[str, Any] = {}

    # Load global config
    global_path = get_global_config_path()
    if global_path.exists():
        global_config = load_yaml_file(global_path)
        config = deep_merge(config, global_config)

    # Load project config (takes precedence)
    project_path = get_project_config_path(cwd)
    if project_path:
        project_config = load_yaml_file(project_path)
        config = deep_merge(config, project_config)

    return config


def create_default_config() -> dict[str, Any]:
    """Create default configuration structure.

    Returns a config dict with all available options and their defaults.
    """
    return {
        "server": {
            "host": "127.0.0.1",
            "port": 8000,
            "log_level": "info",
            "max_sessions": 100,
            "session_timeout_seconds": 3600.0,
        },
        "llm": {
            "provider": "mock",
            "model": "gpt-4o",
            "temperature": None,
            "max_tokens": None,
            "system_prompt": None,
        },
        "workspace": {
            "root": "./workspaces",
        },
        "rate_limit": {
            "per_minute": 60,
            "burst": 10,
        },
        "observability": {
            "otel_endpoint": None,
            "metrics_port": 9090,
        },
    }


def save_config(config: dict[str, Any], path: Path) -> None:
    """Save configuration to YAML file."""
    if not HAS_YAML:
        raise ImportError(
            "PyYAML is required for YAML config support. Install with: uv pip install pyyaml"
        )

    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def init_global_config() -> Path:
    """Initialize global configuration file with defaults.

    Returns path to created config file.
    """
    config_path = get_global_config_path()

    if config_path.exists():
        return config_path

    default_config = create_default_config()
    save_config(default_config, config_path)

    return config_path


def init_project_config(project_path: Path) -> Path:
    """Initialize project configuration file with defaults.

    Args:
        project_path: Path to project directory

    Returns path to created config file.
    """
    config_dir = project_path / ".cognition"
    config_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "config.yaml"

    if config_path.exists():
        return config_path

    # Create minimal project config (only overrides)
    project_config = {
        "llm": {
            # Project-specific defaults
            "system_prompt": "You are a helpful AI coding assistant.",
        },
    }

    save_config(project_config, config_path)

    return config_path


class ConfigLoader:
    """Configuration loader with caching.

    Loads configuration from YAML files and provides merged settings.
    """

    def __init__(self, cwd: Path | None = None):
        self.cwd = cwd or Path.cwd()
        self._config: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        """Load and return merged configuration."""
        if self._config is None:
            self._config = load_config(self.cwd)
        return self._config

    def reload(self) -> dict[str, Any]:
        """Reload configuration from disk."""
        self._config = None
        return self.load()

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key (dot notation supported).

        Example: loader.get("llm.provider") returns "openai"
        """
        config = self.load()
        keys = key.split(".")

        value = config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    def to_env_vars(self) -> dict[str, str]:
        """Convert configuration to environment variable format.

        Returns dict of COGNITION_* env vars for use with Settings.
        """
        config = self.load()
        env_vars: dict[str, str] = {}

        # Map config keys to env var names
        mapping = {
            ("server", "host"): "COGNITION_HOST",
            ("server", "port"): "COGNITION_PORT",
            ("server", "log_level"): "COGNITION_LOG_LEVEL",
            ("server", "max_sessions"): "COGNITION_MAX_SESSIONS",
            ("server", "session_timeout_seconds"): "COGNITION_SESSION_TIMEOUT_SECONDS",
            ("llm", "provider"): "COGNITION_LLM_PROVIDER",
            ("llm", "model"): "COGNITION_LLM_MODEL",
            ("llm", "temperature"): "COGNITION_LLM_TEMPERATURE",
            ("llm", "max_tokens"): "COGNITION_LLM_MAX_TOKENS",
            ("llm", "system_prompt"): "COGNITION_LLM_SYSTEM_PROMPT",
            ("workspace", "root"): "COGNITION_WORKSPACE_ROOT",
            ("rate_limit", "per_minute"): "COGNITION_RATE_LIMIT_PER_MINUTE",
            ("rate_limit", "burst"): "COGNITION_RATE_LIMIT_BURST",
            ("observability", "otel_endpoint"): "COGNITION_OTEL_ENDPOINT",
            ("observability", "metrics_port"): "COGNITION_METRICS_PORT",
            ("agent", "memory"): "COGNITION_AGENT_MEMORY",
            ("agent", "skills"): "COGNITION_AGENT_SKILLS",
            ("agent", "subagents"): "COGNITION_AGENT_SUBAGENTS",
            ("agent", "interrupt_on"): "COGNITION_AGENT_INTERRUPT_ON",
            ("openai_compatible", "base_url"): "COGNITION_OPENAI_COMPATIBLE_BASE_URL",
            ("openai_compatible", "api_key"): "COGNITION_OPENAI_COMPATIBLE_API_KEY",
        }

        for keys, env_name in mapping.items():
            value = self.get(".".join(keys))
            if value is not None:
                if isinstance(value, (list, dict)):
                    env_vars[env_name] = json.dumps(value)
                else:
                    env_vars[env_name] = str(value)

        return env_vars
