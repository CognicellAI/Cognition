"""Unit tests for settings module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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

    def test_default_a2a_security_discovery_is_empty(self):
        settings = TestSettings()
        assert settings.a2a_security_schemes == {}
        assert settings.a2a_security_requirements == []
        assert settings.a2a_max_raw_part_bytes == 10 * 1024 * 1024
        assert settings.a2a_max_parts == 64
        assert settings.a2a_max_message_bytes == 16 * 1024 * 1024
        assert settings.a2a_stream_chunk_bytes == 4096
        assert settings.a2a_terminal_task_ttl_seconds == 0


class TestA2ASecuritySettings:
    """Test JSON environment parsing for public Agent Card security metadata."""

    def test_parses_a2a_security_environment_json(self, monkeypatch: pytest.MonkeyPatch):
        schemes = {
            "oauth2": {
                "oauth2SecurityScheme": {
                    "flows": {
                        "clientCredentials": {
                            "tokenUrl": "https://auth.example.com/oauth/token",
                            "scopes": {"a2a.invoke": "Invoke the agent"},
                        }
                    }
                }
            }
        }
        requirements: list[dict[str, Any]] = [{"schemes": {"oauth2": {}}}]
        monkeypatch.setenv("COGNITION_A2A_SECURITY_SCHEMES", json.dumps(schemes))
        monkeypatch.setenv(
            "COGNITION_A2A_SECURITY_REQUIREMENTS",
            json.dumps(requirements),
        )

        settings = TestSettings()

        assert settings.a2a_security_schemes == schemes
        assert settings.a2a_security_requirements == requirements

    def test_default_durable_file_backend_is_local(self):
        """Local and development profiles retain an explicit local backend."""
        settings = TestSettings()
        assert settings.durable_file_backend == "local"
        assert not settings.s3_enabled

    def test_s3_compatible_durable_file_configuration(self):
        """Garage-style endpoint settings select the generic S3 backend."""
        settings = TestSettings(
            durable_file_backend="s3",
            s3_bucket="cognition",
            s3_endpoint_url="http://garage:3900",
            s3_force_path_style=True,
        )
        assert settings.s3_enabled
        assert settings.s3_force_path_style


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

    def test_aws_lambda_microvm_sandbox_backend_is_valid(self):
        """AWS Lambda MicroVM is a recognized sandbox backend option."""
        settings = TestSettings(sandbox_backend="aws_lambda_microvm")
        assert settings.sandbox_backend == "aws_lambda_microvm"
        assert settings.aws_lambda_microvm_default_profile == "default"


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
