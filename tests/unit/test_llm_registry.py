"""Unit tests for LLM provider registry factories.

Focus on Bedrock credential resolution paths:
- Ambient credentials (no keys set → boto3 credential chain)
- Explicit static keys (both key + secret)
- Session token for STS temporary credentials
- Partial keys (only one of key/secret) → ValueError
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


def _make_config(model: str = "anthropic.claude-3-sonnet-20240229-v1:0") -> MagicMock:
    cfg = MagicMock()
    cfg.model = model
    cfg.region = None
    return cfg


class TestBedrockCredentialResolution:
    """Tests for create_bedrock_model() credential handling."""

    def test_no_keys_omits_credentials_from_kwargs(self):
        """When no keys are set, credential kwargs must be absent so boto3 uses ambient chain."""
        settings = TestSettings(llm_provider="bedrock")
        assert settings.aws_access_key_id is None
        assert settings.aws_secret_access_key is None

        with patch("langchain_aws.ChatBedrock") as mock_bedrock:
            mock_bedrock.return_value = MagicMock()
            from server.app.llm.registry import create_bedrock_model

            create_bedrock_model(_make_config(), settings)

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
            llm_provider="bedrock",
            aws_access_key_id=SecretStr("AKIAIOSFODNN7EXAMPLE"),
            aws_secret_access_key=SecretStr("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
        )

        with patch("langchain_aws.ChatBedrock") as mock_bedrock:
            mock_bedrock.return_value = MagicMock()
            from server.app.llm.registry import create_bedrock_model

            create_bedrock_model(_make_config(), settings)

        _, kwargs = mock_bedrock.call_args
        assert kwargs["aws_access_key_id"] == "AKIAIOSFODNN7EXAMPLE"
        assert kwargs["aws_secret_access_key"] == "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        assert "aws_session_token" not in kwargs

    def test_session_token_passed_alongside_keys(self):
        """When AWS_SESSION_TOKEN is set along with key+secret, all three are forwarded."""
        settings = TestSettings(
            llm_provider="bedrock",
            aws_access_key_id=SecretStr("ASIA_EXAMPLE_KEY"),
            aws_secret_access_key=SecretStr("secret"),
            aws_session_token=SecretStr("AQoDYXdzENH...token"),
        )

        with patch("langchain_aws.ChatBedrock") as mock_bedrock:
            mock_bedrock.return_value = MagicMock()
            from server.app.llm.registry import create_bedrock_model

            create_bedrock_model(_make_config(), settings)

        _, kwargs = mock_bedrock.call_args
        assert kwargs["aws_access_key_id"] == "ASIA_EXAMPLE_KEY"
        assert kwargs["aws_secret_access_key"] == "secret"
        assert kwargs["aws_session_token"] == "AQoDYXdzENH...token"

    def test_partial_keys_raises_valueerror(self):
        """Only one of key/secret set must raise a clear ValueError immediately."""
        settings_key_only = TestSettings(
            llm_provider="bedrock",
            aws_access_key_id=SecretStr("AKIAIOSFODNN7EXAMPLE"),
        )
        settings_secret_only = TestSettings(
            llm_provider="bedrock",
            aws_secret_access_key=SecretStr("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
        )

        from server.app.llm.registry import create_bedrock_model

        with pytest.raises(ValueError, match="Both AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY"):
            create_bedrock_model(_make_config(), settings_key_only)

        with pytest.raises(ValueError, match="Both AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY"):
            create_bedrock_model(_make_config(), settings_secret_only)

    def test_role_arn_calls_sts_assume_role(self):
        """When bedrock_role_arn is set, sts:AssumeRole must be called and temp creds used."""
        settings = TestSettings(
            llm_provider="bedrock",
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
            from server.app.llm.registry import create_bedrock_model

            create_bedrock_model(_make_config(), settings)

        # sts client was created
        mock_boto3_client.assert_called_once_with("sts", region_name=settings.aws_region)
        # assume_role was called with the configured ARN
        mock_sts.assume_role.assert_called_once_with(
            RoleArn="arn:aws:iam::123456789012:role/CognitionBedrockRole",
            RoleSessionName="cognition-bedrock-session",
        )
        # Assumed temporary credentials were passed to ChatBedrock
        _, kwargs = mock_bedrock.call_args
        assert kwargs["aws_access_key_id"] == "ASIA_ASSUMED_KEY"
        assert kwargs["aws_secret_access_key"] == "assumed-secret"
        assert kwargs["aws_session_token"] == "assumed-token"

    def test_role_arn_takes_precedence_over_explicit_keys(self):
        """When both role_arn and explicit keys are set, role assumption wins."""
        settings = TestSettings(
            llm_provider="bedrock",
            aws_access_key_id=SecretStr("EXPLICIT_KEY"),
            aws_secret_access_key=SecretStr("explicit-secret"),
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
            patch("boto3.client", return_value=mock_sts),
            patch("langchain_aws.ChatBedrock") as mock_bedrock,
        ):
            mock_bedrock.return_value = MagicMock()
            from server.app.llm.registry import create_bedrock_model

            create_bedrock_model(_make_config(), settings)

        _, kwargs = mock_bedrock.call_args
        # Must use assumed credentials, not explicit keys
        assert kwargs["aws_access_key_id"] == "ASIA_ASSUMED_KEY"
        assert kwargs["aws_secret_access_key"] == "assumed-secret"
        assert kwargs["aws_session_token"] == "assumed-token"
