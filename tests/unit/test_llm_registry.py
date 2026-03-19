"""Unit tests for Bedrock credential resolution in _build_bedrock_model().

Focus on the credential resolution paths:
- Ambient credentials (no keys set → boto3 credential chain)
- Explicit static keys (both key + secret)
- Session token for STS temporary credentials
- Partial keys (only one of key/secret) → LLMProviderConfigError
- Role assumption via sts:AssumeRole
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from server.app.settings import Settings


# Isolated settings that never read from .env
class TestSettings(Settings):
    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )


class TestBedrockCredentialResolution:
    """Tests for _build_bedrock_model() credential handling."""

    def test_no_keys_omits_credentials_from_kwargs(self):
        """When no keys are set, credential kwargs must be absent so boto3 uses ambient chain."""
        settings = TestSettings()
        assert settings.aws_access_key_id is None
        assert settings.aws_secret_access_key is None

        with patch("langchain_aws.ChatBedrock") as mock_bedrock:
            mock_bedrock.return_value = MagicMock()
            from server.app.llm.deep_agent_service import _build_bedrock_model

            _build_bedrock_model(
                model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                region=None,
                role_arn=None,
                settings=settings,
            )

        _, kwargs = mock_bedrock.call_args
        assert "aws_access_key_id" not in kwargs, (
            "aws_access_key_id must not be passed when no keys are set — "
            "its presence (even as None) triggers boto3 session creation and can break "
            "the ambient credential chain."
        )
        assert "aws_secret_access_key" not in kwargs
        assert "aws_session_token" not in kwargs

    def test_both_keys_passes_credentials(self):
        """When both key + secret are set, they must be forwarded to ChatBedrock."""
        settings = TestSettings(
            aws_access_key_id=SecretStr("AKIAIOSFODNN7EXAMPLE"),
            aws_secret_access_key=SecretStr("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
        )

        with patch("langchain_aws.ChatBedrock") as mock_bedrock:
            mock_bedrock.return_value = MagicMock()
            from server.app.llm.deep_agent_service import _build_bedrock_model

            _build_bedrock_model(
                model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                region=None,
                role_arn=None,
                settings=settings,
            )

        _, kwargs = mock_bedrock.call_args
        assert kwargs["aws_access_key_id"] == "AKIAIOSFODNN7EXAMPLE"
        assert kwargs["aws_secret_access_key"] == "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        assert "aws_session_token" not in kwargs

    def test_session_token_passed_alongside_keys(self):
        """When AWS_SESSION_TOKEN is set along with key+secret, all three are forwarded."""
        settings = TestSettings(
            aws_access_key_id=SecretStr("ASIA_EXAMPLE_KEY"),
            aws_secret_access_key=SecretStr("secret"),
            aws_session_token=SecretStr("AQoDYXdzENH...token"),
        )

        with patch("langchain_aws.ChatBedrock") as mock_bedrock:
            mock_bedrock.return_value = MagicMock()
            from server.app.llm.deep_agent_service import _build_bedrock_model

            _build_bedrock_model(
                model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                region=None,
                role_arn=None,
                settings=settings,
            )

        _, kwargs = mock_bedrock.call_args
        assert kwargs["aws_access_key_id"] == "ASIA_EXAMPLE_KEY"
        assert kwargs["aws_secret_access_key"] == "secret"
        assert kwargs["aws_session_token"] == "AQoDYXdzENH...token"

    def test_partial_keys_raises_error(self):
        """Only one of key/secret set must raise LLMProviderConfigError immediately."""
        from server.app.exceptions import LLMProviderConfigError
        from server.app.llm.deep_agent_service import _build_bedrock_model

        settings_key_only = TestSettings(
            aws_access_key_id=SecretStr("AKIAIOSFODNN7EXAMPLE"),
        )
        settings_secret_only = TestSettings(
            aws_secret_access_key=SecretStr("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
        )

        with pytest.raises(LLMProviderConfigError, match="Both AWS_ACCESS_KEY_ID"):
            _build_bedrock_model(
                "anthropic.claude-3-sonnet", region=None, role_arn=None, settings=settings_key_only
            )

        with pytest.raises(LLMProviderConfigError, match="Both AWS_ACCESS_KEY_ID"):
            _build_bedrock_model(
                "anthropic.claude-3-sonnet",
                region=None,
                role_arn=None,
                settings=settings_secret_only,
            )

    def test_role_arn_calls_sts_assume_role(self):
        """When role_arn is provided, sts:AssumeRole is called and temp creds used."""
        settings = TestSettings(
            bedrock_role_arn="arn:aws:iam::123456789012:role/CognitionBedrockRole",
        )

        fake_creds = {
            "Credentials": {
                "AccessKeyId": "ASIA_ASSUMED_KEY",
                "SecretAccessKey": "assumed-secret",
                "SessionToken": "assumed-token",
            }
        }
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = fake_creds

        with (
            patch("boto3.client", return_value=mock_sts) as mock_boto3_client,
            patch("langchain_aws.ChatBedrock") as mock_bedrock,
        ):
            mock_bedrock.return_value = MagicMock()
            from server.app.llm.deep_agent_service import _build_bedrock_model

            _build_bedrock_model(
                model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                region=None,
                role_arn=None,  # role_arn comes from settings.bedrock_role_arn
                settings=settings,
            )

        mock_boto3_client.assert_called_once_with("sts", region_name=settings.aws_region)
        mock_sts.assume_role.assert_called_once_with(
            RoleArn="arn:aws:iam::123456789012:role/CognitionBedrockRole",
            RoleSessionName="cognition-bedrock-session",
        )
        _, kwargs = mock_bedrock.call_args
        assert kwargs["aws_access_key_id"] == "ASIA_ASSUMED_KEY"
        assert kwargs["aws_secret_access_key"] == "assumed-secret"
        assert kwargs["aws_session_token"] == "assumed-token"

    def test_explicit_role_arn_param_takes_precedence(self):
        """role_arn passed directly overrides settings.bedrock_role_arn."""
        settings = TestSettings(
            bedrock_role_arn="arn:aws:iam::999:role/SettingsRole",
        )

        fake_creds = {
            "Credentials": {
                "AccessKeyId": "ASSUMED_FROM_EXPLICIT_ARN",
                "SecretAccessKey": "secret",
                "SessionToken": "token",
            }
        }
        mock_sts = MagicMock()
        mock_sts.assume_role.return_value = fake_creds

        with (
            patch("boto3.client", return_value=mock_sts),
            patch("langchain_aws.ChatBedrock") as mock_bedrock,
        ):
            mock_bedrock.return_value = MagicMock()
            from server.app.llm.deep_agent_service import _build_bedrock_model

            _build_bedrock_model(
                model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                region=None,
                role_arn="arn:aws:iam::123:role/ExplicitRole",  # explicit overrides settings
                settings=settings,
            )

        call_kwargs = mock_sts.assume_role.call_args[1]
        assert call_kwargs["RoleArn"] == "arn:aws:iam::123:role/ExplicitRole"
