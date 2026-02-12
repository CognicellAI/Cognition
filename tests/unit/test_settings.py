"""Unit tests for settings module."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from server.app.settings import Settings


# Test settings class that doesn't load from env file
class TestSettings(Settings):
    """Test settings that don't load from env file."""

    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,  # Allow population by field name, not just alias
    )


class TestSettingsDefaults:
    """Test default settings values."""

    def test_default_server_settings(self):
        """Test default server settings."""
        settings = TestSettings()
        assert settings.host == "127.0.0.1"
        assert settings.port == 8000
        assert settings.log_level == "info"

    def test_default_workspace_root(self):
        """Test default workspace root."""
        settings = TestSettings()
        assert isinstance(settings.workspace_root, Path)
        assert settings.workspace_root.is_absolute()

    def test_default_llm_settings(self):
        """Test default LLM settings."""
        settings = TestSettings()
        assert settings.llm_provider == "mock"
        assert settings.llm_model == "gpt-4o"

    def test_default_session_settings(self):
        """Test default session settings."""
        settings = TestSettings()
        assert settings.max_sessions == 100
        assert settings.session_timeout_seconds == 3600.0

    def test_default_rate_limiting(self):
        """Test default rate limiting settings."""
        settings = TestSettings()
        assert settings.rate_limit_per_minute == 60
        assert settings.rate_limit_burst == 10

    def test_default_observability(self):
        """Test default observability settings."""
        settings = TestSettings()
        assert settings.otel_endpoint is None
        assert settings.metrics_port == 9090


class TestSettingsSecrets:
    """Test SecretStr handling in settings."""

    def test_openai_api_key_is_secret(self):
        """Test that OpenAI API key is stored as SecretStr."""
        settings = TestSettings(openai_api_key="sk-test-key")
        assert isinstance(settings.openai_api_key, SecretStr)
        # Should not be directly accessible
        assert settings.openai_api_key.get_secret_value() == "sk-test-key"

    def test_aws_credentials_are_secret(self):
        """Test that AWS credentials are stored as SecretStr."""
        settings = TestSettings(
            aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
            aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        )
        assert isinstance(settings.aws_access_key_id, SecretStr)
        assert isinstance(settings.aws_secret_access_key, SecretStr)
        assert settings.aws_access_key_id.get_secret_value() == "AKIAIOSFODNN7EXAMPLE"
        assert (
            settings.aws_secret_access_key.get_secret_value()
            == "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        )

    def test_compatible_api_key_is_secret(self):
        """Test that OpenAI compatible API key is stored as SecretStr."""
        settings = TestSettings(openai_compatible_api_key="custom-key")
        assert isinstance(settings.openai_compatible_api_key, SecretStr)


class TestSettingsValidation:
    """Test settings validation."""

    def test_workspace_root_resolves_to_absolute(self):
        """Test that relative workspace root is resolved to absolute."""
        settings = TestSettings(workspace_root=Path("./relative/path"))
        assert settings.workspace_root.is_absolute()

    def test_port_validation_valid(self):
        """Test valid port numbers."""
        settings = TestSettings(port=8080)
        assert settings.port == 8080

    def test_port_validation_too_low(self):
        """Test that port < 1 raises validation error."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError) as exc_info:
            TestSettings(port=0)
        assert "Port must be between" in str(exc_info.value)

    def test_port_validation_too_high(self):
        """Test that port > 65535 raises validation error."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError) as exc_info:
            TestSettings(port=70000)
        assert "Port must be between" in str(exc_info.value)

    def test_metrics_port_validation(self):
        """Test metrics port validation."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            TestSettings(metrics_port=0)

    def test_max_sessions_validation_positive(self):
        """Test that max_sessions must be positive."""
        settings = TestSettings(max_sessions=10)
        assert settings.max_sessions == 10

    def test_max_sessions_validation_zero(self):
        """Test that max_sessions=0 raises validation error."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError) as exc_info:
            TestSettings(max_sessions=0)
        assert "max_sessions must be at least 1" in str(exc_info.value)

    def test_max_sessions_validation_negative(self):
        """Test that negative max_sessions raises validation error."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            TestSettings(max_sessions=-1)

    def test_timeout_validation_positive(self):
        """Test that timeout must be positive."""
        settings = TestSettings(session_timeout_seconds=1800.0)
        assert settings.session_timeout_seconds == 1800.0

    def test_timeout_validation_zero(self):
        """Test that timeout=0 raises validation error."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError) as exc_info:
            TestSettings(session_timeout_seconds=0.0)
        assert "session_timeout_seconds must be positive" in str(exc_info.value)

    def test_timeout_validation_negative(self):
        """Test that negative timeout raises validation error."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            TestSettings(session_timeout_seconds=-1.0)


class TestLLMProviderSettings:
    """Test LLM provider specific settings."""

    def test_openai_provider_settings(self):
        """Test OpenAI provider configuration."""
        settings = TestSettings(
            llm_provider="openai",
            llm_model="gpt-4",
            openai_api_key="sk-test",
            openai_api_base="https://api.openai.com/v1",
        )
        assert settings.llm_provider == "openai"
        assert settings.llm_model == "gpt-4"

    def test_bedrock_provider_settings(self):
        """Test Bedrock provider configuration."""
        settings = TestSettings(
            llm_provider="bedrock",
            aws_region="us-west-2",
            bedrock_model_id="anthropic.claude-3-sonnet",
        )
        assert settings.llm_provider == "bedrock"
        assert settings.aws_region == "us-west-2"

    def test_openai_compatible_settings(self):
        """Test OpenAI compatible provider configuration."""
        settings = TestSettings(
            llm_provider="openai_compatible",
            openai_compatible_base_url="http://localhost:8000/v1",
            openai_compatible_api_key="custom-key",
        )
        assert settings.llm_provider == "openai_compatible"
        assert settings.openai_compatible_base_url == "http://localhost:8000/v1"

    def test_mock_provider_default(self):
        """Test that mock is the default provider."""
        settings = TestSettings()
        assert settings.llm_provider == "mock"


class TestSettingsLLMModel:
    """Test get_llm_model method."""

    @pytest.mark.skip(reason="Requires langchain dependencies")
    def test_get_mock_model(self):
        """Test getting mock model."""
        settings = TestSettings(llm_provider="mock")
        model = settings.get_llm_model()
        assert model is not None

    @pytest.mark.skip(reason="Requires langchain dependencies")
    def test_get_llm_model_extracts_secrets(self):
        """Test that get_llm_model extracts secrets from SecretStr."""
        # This test just verifies the method runs without error
        # The actual model creation requires optional dependencies
        settings = TestSettings(
            llm_provider="mock",  # Use mock to avoid import issues
        )
        model = settings.get_llm_model()
        assert model is not None
