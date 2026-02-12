"""Application settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Server settings
    host: str = Field(default="127.0.0.1", alias="COGNITION_HOST")
    port: int = Field(default=8000, alias="COGNITION_PORT")
    log_level: str = Field(default="info", alias="COGNITION_LOG_LEVEL")

    # Workspace settings
    workspace_root: Path = Field(
        default=Path("./workspaces"),
        alias="COGNITION_WORKSPACE_ROOT",
    )

    # LLM settings
    llm_provider: Literal["openai", "bedrock", "mock"] = Field(
        default="mock",
        alias="COGNITION_LLM_PROVIDER",
    )
    llm_model: str = Field(default="gpt-4o", alias="COGNITION_LLM_MODEL")

    # OpenAI settings
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    openai_api_base: Optional[str] = Field(default=None, alias="OPENAI_API_BASE")

    # Bedrock settings
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    aws_access_key_id: Optional[str] = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: Optional[str] = Field(default=None, alias="AWS_SECRET_ACCESS_KEY")
    bedrock_model_id: str = Field(
        default="anthropic.claude-3-sonnet-20240229-v1:0",
        alias="COGNITION_BEDROCK_MODEL_ID",
    )

    # Ollama settings (for local testing)
    ollama_model: str = Field(default="llama3.2", alias="COGNITION_OLLAMA_MODEL")
    ollama_base_url: str = Field(
        default="http://localhost:11434", alias="COGNITION_OLLAMA_BASE_URL"
    )

    # Session settings
    max_sessions: int = Field(default=100, alias="COGNITION_MAX_SESSIONS")
    session_timeout_seconds: float = Field(
        default=3600.0,
        alias="COGNITION_SESSION_TIMEOUT_SECONDS",
    )

    # Test settings
    test_llm_mode: Literal["mock", "openai", "ollama"] = Field(
        default="mock",
        alias="COGNITION_TEST_LLM_MODE",
    )

    def get_llm_model(self):
        """Get the LLM model instance based on provider."""
        if self.llm_provider == "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=self.llm_model,
                api_key=self.openai_api_key,
                base_url=self.openai_api_base,
            )
        elif self.llm_provider == "bedrock":
            from langchain_aws import ChatBedrock

            return ChatBedrock(
                model_id=self.bedrock_model_id,
                region_name=self.aws_region,
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
            )
        elif self.llm_provider == "mock":
            from server.app.llm.mock import MockLLM

            return MockLLM()
        else:
            raise ValueError(f"Unknown LLM provider: {self.llm_provider}")


# Global settings instance
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get the global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
