"""YAML configuration loader.

Supports hierarchical configuration:
1. Built-in defaults
2. ~/.cognition/config.yaml (global user prefs)
3. .cognition/config.yaml (project-level)
4. Environment variables / .env (highest precedence)

All configuration mappings are auto-generated from Settings class definitions.
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
            import yaml

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


def _get_settings_schema() -> list[dict[str, Any]]:
    """Get Settings field schema with mappings.

    This function introspects the Settings class to derive:
    - Field name (e.g., 'llm_provider')
    - Environment variable name from Field alias (e.g., 'COGNITION_LLM_PROVIDER')
    - YAML path (e.g., ['llm', 'provider'])
    - Default value
    - Field type

    Returns list of field schemas.
    """
    # Import here to avoid circular imports
    from pydantic.fields import FieldInfo

    # Create a temporary settings instance to inspect fields
    # We don't use get_settings() because that triggers the full loading
    from server.app.settings import Settings

    schema = []
    for field_name, field_info in Settings.model_fields.items():
        field_info: FieldInfo

        # Get env var from alias
        env_var = field_info.alias
        if not env_var:
            # If no alias, derive from field name (COGNITION_<field_name.upper()>)
            env_var = f"COGNITION_{field_name.upper()}"

        # Derive YAML path from field name
        # Format: <section>_<key> where key can contain underscores
        # Section is the logical grouping (first word or special multi-word section)

        # Map field names to YAML sections
        section_mapping = {
            # Server settings
            "host": "server",
            "port": "server",
            "log_level": "server",
            "max_sessions": "server",
            "session_timeout_seconds": "server",
            # LLM settings
            "llm": "llm",
            # Provider-specific settings
            "openai": "openai",
            "aws": "aws",
            "bedrock": "bedrock",
            "ollama": "ollama",
            "openai_compatible": "openai_compatible",
            # Workspace settings
            "workspace": "workspace",
            # Rate limiting
            "rate": "rate_limit",
            # Observability
            "otel": "observability",
            "metrics": "observability",
            # Agent settings
            "agent": "agent",
            # Persistence
            "persistence": "persistence",
            # Test settings
            "test": "test",
        }

        # Find which section this field belongs to
        parts = field_name.split("_")
        section = None

        # Check for multi-word section names first
        if len(parts) >= 2:
            two_word_prefix = f"{parts[0]}_{parts[1]}"
            if two_word_prefix in ("openai_compatible", "rate_limit", "session_timeout"):
                if two_word_prefix == "openai_compatible":
                    section = "openai_compatible"
                    # Keep the full remaining key: openai_compatible_base_url -> base_url
                    key = "_".join(parts[2:]) if len(parts) > 2 else field_name
                elif two_word_prefix == "rate_limit":
                    section = "rate_limit"
                    # rate_limit_per_minute -> per_minute
                    key = "_".join(parts[2:]) if len(parts) > 2 else field_name
                else:  # session_timeout_seconds
                    section = "server"
                    key = field_name
            elif parts[0] in section_mapping:
                section = section_mapping[parts[0]]
                # For observability fields, keep the full original field name as key
                # otel_endpoint -> otel_endpoint (not 'endpoint')
                # metrics_port -> metrics_port (not 'port')
                if section == "observability":
                    key = field_name
                else:
                    key = "_".join(parts[1:]) if len(parts) > 1 else field_name
            else:
                # Fallback to server section
                section = "server"
                key = field_name
        elif parts[0] in section_mapping:
            section = section_mapping[parts[0]]
            key = field_name
        else:
            section = "server"
            key = field_name

        yaml_path = [section, key]

        # Get default value
        default = field_info.default
        if default is None:
            # Get default from factory if available
            default_factory = getattr(field_info, "default_factory", None)
            if default_factory is not None:
                # type: ignore
                default = default_factory() if callable(default_factory) else default_factory

        # Skip SecretStr fields from config generation (security)
        # but still include them in the mapping for env vars
        from pydantic import SecretStr

        is_secret = "SecretStr" in str(field_info.annotation)

        schema.append(
            {
                "field_name": field_name,
                "env_var": env_var,
                "yaml_path": yaml_path,
                "default": default,
                "annotation": str(field_info.annotation),
                "is_secret": is_secret,
            }
        )

    return schema


def _build_nested_config(
    schema: list[dict[str, Any]], include_secrets: bool = False
) -> dict[str, Any]:
    """Build nested config dict from schema.

    Args:
        schema: List of field schemas from _get_settings_schema()
        include_secrets: Whether to include SecretStr fields (default False for safety)

    Returns nested dict structure suitable for YAML.
    """
    config: dict[str, Any] = {}

    for field in schema:
        if field["is_secret"] and not include_secrets:
            continue

        yaml_path = field["yaml_path"]
        value = field["default"]

        # Navigate to the right nested level
        current = config
        for key in yaml_path[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        # Set the value at the leaf
        current[yaml_path[-1]] = value

    return config


def create_default_config() -> dict[str, Any]:
    """Create default configuration structure from Settings defaults.

    Returns a config dict with all available options and their defaults.
    """
    schema = _get_settings_schema()
    return _build_nested_config(schema, include_secrets=False)


def save_config(config: dict[str, Any], path: Path) -> None:
    """Save configuration to YAML file."""
    if not HAS_YAML:
        raise ImportError(
            "PyYAML is required for YAML config support. Install with: uv pip install pyyaml"
        )

    import yaml

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
    """Initialize project configuration file with minimal defaults.

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
        self._schema: list[dict[str, Any]] | None = None

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

    def _get_mapping(self) -> dict[tuple[str, ...], str]:
        """Get auto-generated mapping from YAML paths to env var names.

        This is derived from Settings field definitions.
        """
        if self._schema is None:
            self._schema = _get_settings_schema()

        mapping: dict[tuple[str, ...], str] = {}
        for field in self._schema:
            yaml_path = tuple(field["yaml_path"])
            env_var = field["env_var"]
            mapping[yaml_path] = env_var

        return mapping

    def to_env_vars(self) -> dict[str, str]:
        """Convert configuration to environment variable format.

        Returns dict of COGNITION_* env vars for use with Settings.
        """
        config = self.load()
        env_vars: dict[str, str] = {}

        # Get auto-generated mapping
        mapping = self._get_mapping()

        for yaml_path, env_name in mapping.items():
            value = self.get(".".join(yaml_path))
            if value is not None:
                if isinstance(value, (list, dict)):
                    env_vars[env_name] = json.dumps(value)
                else:
                    env_vars[env_name] = str(value)

        return env_vars


def generate_config_example() -> str:
    """Generate a comprehensive config.example.yaml with all settings and documentation.

    Returns the YAML content as a string.
    """
    schema = _get_settings_schema()

    lines = [
        "# Cognition Configuration File",
        "#",
        "# This file defines all available configuration options.",
        "# Copy this file to .cognition/config.yaml and customize as needed.",
        "#",
        "# Configuration hierarchy (lowest to highest precedence):",
        "#   1. Built-in defaults",
        "#   2. ~/.cognition/config.yaml (global user preferences)",
        "#   3. .cognition/config.yaml (project-level)",
        "#   4. Environment variables / .env (highest precedence)",
        "#",
        "# Security note: Never commit API keys or secrets to this file.",
        "# Use environment variables for secrets.",
        "",
    ]

    # Build nested structure with comments
    config = _build_nested_config(schema, include_secrets=False)

    # Add comments for each section
    section_comments = {
        "server": [
            "# Server settings",
            "# Configure the HTTP server that serves the Cognition API.",
        ],
        "llm": [
            "# LLM (Language Model) settings",
            "# Configure which AI model provider to use and model-specific options.",
            "#",
            "# Supported providers: mock, openai, bedrock, openai_compatible, ollama",
            "#",
            "# Note: API keys should be set via environment variables:",
            "#   OPENAI_API_KEY, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, etc.",
        ],
        "openai": [
            "# OpenAI-specific settings",
        ],
        "aws": [
            "# AWS/Bedrock-specific settings",
        ],
        "bedrock": [
            "# Bedrock model settings",
        ],
        "ollama": [
            "# Ollama (local LLM) settings",
        ],
        "openai_compatible": [
            "# OpenAI-compatible API settings (for self-hosted models)",
        ],
        "workspace": [
            "# Workspace settings",
            "# Configure where projects/workspaces are stored.",
        ],
        "rate_limit": [
            "# Rate limiting settings",
            "# Control API request throttling.",
        ],
        "observability": [
            "# Observability settings",
            "# Configure metrics, tracing, and monitoring.",
        ],
        "agent": [
            "# Agent behavior settings",
            "# Configure how the AI agent behaves and what capabilities it has.",
        ],
        "persistence": [
            "# Persistence settings",
            "# Configure how session state is stored.",
        ],
        "test": [
            "# Test settings",
            "# Configure testing behavior.",
        ],
    }

    def serialize_value(key: str, value: Any, indent: int = 0) -> list[str]:
        """Serialize a value to YAML format."""
        prefix = "  " * indent

        if value is None:
            return [f"{prefix}{key}: null"]
        elif isinstance(value, bool):
            return [f"{prefix}{key}: {str(value).lower()}"]
        elif isinstance(value, (int, float)):
            return [f"{prefix}{key}: {value}"]
        elif isinstance(value, str):
            # Quote strings that might look like other types
            if any(
                c in value
                for c in [
                    ":",
                    "#",
                    "{",
                    "}",
                    "[",
                    "]",
                    ",",
                    "&",
                    "*",
                    "?",
                    "|",
                    "-",
                    "<",
                    ">",
                    "=",
                    "!",
                    "%",
                    "@",
                    "`",
                    '"',
                    "'",
                ]
            ):
                return [f'{prefix}{key}: "{value}"']
            return [f"{prefix}{key}: {value}"]
        elif isinstance(value, list):
            if not value:
                return [f"{prefix}{key}: []"]
            lines = [f"{prefix}{key}:"]
            for item in value:
                if isinstance(item, str):
                    lines.append(f'{prefix}  - "{item}"')
                else:
                    lines.append(f"{prefix}  - {item}")
            return lines
        elif isinstance(value, dict):
            if not value:
                return [f"{prefix}{key}: {{}}"]
            lines = [f"{prefix}{key}:"]
            for k, v in value.items():
                lines.extend(serialize_value(k, v, indent + 1))
            return lines
        else:
            return [f"{prefix}{key}: {value}"]

    # Build output with section headers
    for section, values in config.items():
        # Add section comments
        if section in section_comments:
            lines.extend(section_comments[section])

        # Add section header
        lines.append(f"{section}:")

        # Add section contents
        for key, value in values.items():
            lines.extend(serialize_value(key, value, indent=1))

        lines.append("")  # Empty line between sections

    return "\n".join(lines)
