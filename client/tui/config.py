"""Client configuration and settings."""

from typing import Final

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ClientSettings(BaseSettings):
    """Configuration for the TUI client.

    Reads from environment variables and .env file.
    All settings prefixed with COGNITION_.
    """

    model_config = SettingsConfigDict(
        env_prefix="COGNITION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server settings
    server_host: str = Field(default="localhost", description="Server hostname")
    server_port: int = Field(default=8000, description="Server port")
    use_ssl: bool = Field(default=False, description="Use wss:// and https://")

    # Default session settings
    default_network_mode: str = Field(
        default="OFF", description="Default network mode for sessions"
    )

    # WebSocket settings
    ws_reconnect_attempts: int = Field(default=5, description="Max reconnection attempts")
    ws_reconnect_delay: float = Field(
        default=1.0, description="Initial reconnection delay (seconds)"
    )
    ws_heartbeat_interval: float = Field(default=30.0, description="Heartbeat interval (seconds)")

    @property
    def base_url(self) -> str:
        """Build base HTTP URL from settings."""
        protocol = "https" if self.use_ssl else "http"
        return f"{protocol}://{self.server_host}:{self.server_port}"

    @property
    def ws_url(self) -> str:
        """Build WebSocket URL from settings."""
        protocol = "wss" if self.use_ssl else "ws"
        return f"{protocol}://{self.server_host}:{self.server_port}/ws"

    @property
    def api_base(self) -> str:
        """Base path for REST API endpoints."""
        return f"{self.base_url}/api"


# Global settings instance
settings: Final[ClientSettings] = ClientSettings()
