"""Per-session model configuration.

Allows overriding temperature, max_tokens, system prompt,
and model selection on a per-session basis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ModelConfig:
    """Per-session model configuration overrides.

    Any field set to None uses the global default from Settings.
    """

    # Model selection
    provider: Optional[str] = None
    model: Optional[str] = None

    # Generation parameters
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None

    # System prompt
    system_prompt: Optional[str] = None
    system_prompt_append: Optional[str] = None  # Appended to base prompt

    # Context management
    max_context_tokens: Optional[int] = None  # Override model's context limit

    def merge_with_defaults(self, defaults: ModelConfig) -> ModelConfig:
        """Create a new config merging this with defaults.

        Values in self take precedence over defaults.

        Args:
            defaults: Default configuration to fill in unset values.

        Returns:
            New ModelConfig with merged values.
        """
        return ModelConfig(
            provider=self.provider or defaults.provider,
            model=self.model or defaults.model,
            temperature=(
                self.temperature if self.temperature is not None else defaults.temperature
            ),
            max_tokens=(self.max_tokens if self.max_tokens is not None else defaults.max_tokens),
            top_p=self.top_p if self.top_p is not None else defaults.top_p,
            system_prompt=self.system_prompt or defaults.system_prompt,
            system_prompt_append=(self.system_prompt_append or defaults.system_prompt_append),
            max_context_tokens=(
                self.max_context_tokens
                if self.max_context_tokens is not None
                else defaults.max_context_tokens
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary, excluding None values."""
        result = {}
        for key, value in self.__dict__.items():
            if value is not None:
                result[key] = value
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelConfig:
        """Create from a dictionary.

        Args:
            data: Dictionary with config values.

        Returns:
            ModelConfig instance.
        """
        valid_keys = {
            "provider",
            "model",
            "temperature",
            "max_tokens",
            "top_p",
            "system_prompt",
            "system_prompt_append",
            "max_context_tokens",
        }
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


class ModelConfigManager:
    """Manages model configurations per session.

    Example:
        manager = ModelConfigManager()
        manager.set_session_config("sess-123", ModelConfig(temperature=0.7))
        config = manager.get_effective_config("sess-123")
    """

    def __init__(self, default_config: Optional[ModelConfig] = None):
        self._default = default_config or ModelConfig()
        self._session_configs: dict[str, ModelConfig] = {}

    @property
    def default_config(self) -> ModelConfig:
        """The global default configuration."""
        return self._default

    def set_default_config(self, config: ModelConfig) -> None:
        """Update the global default configuration.

        Args:
            config: New default configuration.
        """
        self._default = config

    def set_session_config(self, session_id: str, config: ModelConfig) -> None:
        """Set or update configuration for a specific session.

        Args:
            session_id: Session identifier.
            config: Configuration overrides for this session.
        """
        self._session_configs[session_id] = config
        logger.debug(
            "Session config updated",
            session_id=session_id,
            config=config.to_dict(),
        )

    def get_session_config(self, session_id: str) -> Optional[ModelConfig]:
        """Get the raw session-specific config (without defaults).

        Args:
            session_id: Session identifier.

        Returns:
            Session config if set, None otherwise.
        """
        return self._session_configs.get(session_id)

    def get_effective_config(self, session_id: str) -> ModelConfig:
        """Get the effective config for a session (merged with defaults).

        Args:
            session_id: Session identifier.

        Returns:
            ModelConfig with session overrides merged over defaults.
        """
        session_config = self._session_configs.get(session_id)
        if session_config:
            return session_config.merge_with_defaults(self._default)
        return self._default

    def clear_session_config(self, session_id: str) -> None:
        """Remove session-specific configuration.

        Args:
            session_id: Session identifier.
        """
        self._session_configs.pop(session_id, None)


# Global manager instance
_config_manager: ModelConfigManager | None = None


def get_model_config_manager() -> ModelConfigManager:
    """Get the global model config manager instance."""
    global _config_manager
    if _config_manager is None:
        _config_manager = ModelConfigManager()
    return _config_manager
