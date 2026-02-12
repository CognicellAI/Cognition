"""AWS Bedrock client factory with support for API keys and IAM roles."""

from __future__ import annotations

from typing import TYPE_CHECKING

import boto3
import structlog
from botocore.config import Config

if TYPE_CHECKING:
    from server.app.settings import Settings

logger = structlog.get_logger()


class BedrockClientFactory:
    """Factory for creating AWS Bedrock clients with various authentication methods."""

    @staticmethod
    def create_bedrock_client(settings: Settings) -> boto3.client:
        """Create a Bedrock runtime client based on settings.

        Supports:
        - IAM Role authentication (when use_bedrock_iam_role=True)
        - Explicit API keys (aws_access_key_id, aws_secret_access_key)
        - AWS Profile (aws_profile)
        - Default credential chain (fallback)

        Args:
            settings: Application settings

        Returns:
            boto3 Bedrock runtime client

        Raises:
            RuntimeError: If unable to create client
        """
        try:
            # Configure boto3 with region
            config = Config(
                region_name=settings.aws_region,
                retries={"max_attempts": 3, "mode": "standard"},
            )

            # Method 1: IAM Role (no explicit credentials needed)
            if settings.use_bedrock_iam_role:
                logger.info(
                    "Creating Bedrock client with IAM role",
                    region=settings.aws_region,
                )
                session = boto3.Session()
                client = session.client(
                    service_name="bedrock-runtime",
                    config=config,
                )
                return client

            # Method 2: Explicit credentials
            if settings.aws_access_key_id and settings.aws_secret_access_key:
                logger.info(
                    "Creating Bedrock client with explicit credentials",
                    region=settings.aws_region,
                    access_key_id=settings.aws_access_key_id[:8] + "...",
                )
                session = boto3.Session(
                    aws_access_key_id=settings.aws_access_key_id,
                    aws_secret_access_key=settings.aws_secret_access_key,
                    aws_session_token=settings.aws_session_token,
                    region_name=settings.aws_region,
                )
                client = session.client(
                    service_name="bedrock-runtime",
                    config=config,
                )
                return client

            # Method 3: AWS Profile
            if settings.aws_profile:
                logger.info(
                    "Creating Bedrock client with AWS profile",
                    profile=settings.aws_profile,
                    region=settings.aws_region,
                )
                session = boto3.Session(
                    profile_name=settings.aws_profile,
                    region_name=settings.aws_region,
                )
                client = session.client(
                    service_name="bedrock-runtime",
                    config=config,
                )
                return client

            # Method 4: Default credential chain (environment, instance metadata, etc.)
            logger.info(
                "Creating Bedrock client with default credential chain",
                region=settings.aws_region,
            )
            session = boto3.Session(region_name=settings.aws_region)
            client = session.client(
                service_name="bedrock-runtime",
                config=config,
            )
            return client

        except Exception as e:
            logger.error("Failed to create Bedrock client", error=str(e))
            raise RuntimeError(f"Failed to create Bedrock client: {e}") from e

    @staticmethod
    def test_connection(client: boto3.client) -> bool:
        """Test Bedrock connection by listing available models.

        Args:
            client: Bedrock runtime client

        Returns:
            True if connection successful
        """
        try:
            # Try to list foundation models as a connectivity test
            bedrock = boto3.client("bedrock")
            bedrock.list_foundation_models()
            return True
        except Exception as e:
            logger.warning("Bedrock connection test failed", error=str(e))
            return False
