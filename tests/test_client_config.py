"""Unit tests for client configuration module."""

import os
from pathlib import Path

import pytest

from client.tui.config import ClientSettings, settings


@pytest.mark.unit
class TestClientSettings:
    """Test ClientSettings class."""

    def test_default_settings(self):
        """Test default configuration values."""
        s = ClientSettings()
        assert s.server_host == "localhost"
        assert s.server_port == 8000
        assert s.use_ssl is False
        assert s.default_network_mode == "OFF"
        assert s.ws_reconnect_attempts == 5
        assert s.ws_reconnect_delay == 1.0
        assert s.ws_heartbeat_interval == 30.0

    def test_base_url_property(self):
        """Test base_url property construction."""
        s = ClientSettings(server_host="192.168.1.1", server_port=9000)
        assert s.base_url == "http://192.168.1.1:9000"

    def test_base_url_with_ssl(self):
        """Test base_url with SSL enabled."""
        s = ClientSettings(use_ssl=True)
        assert s.base_url == "https://localhost:8000"

    def test_ws_url_property(self):
        """Test ws_url property construction."""
        s = ClientSettings()
        assert s.ws_url == "ws://localhost:8000/ws"

    def test_ws_url_with_ssl(self):
        """Test ws_url with SSL enabled."""
        s = ClientSettings(use_ssl=True, server_host="example.com", server_port=443)
        assert s.ws_url == "wss://example.com:443/ws"

    def test_api_base_property(self):
        """Test api_base property construction."""
        s = ClientSettings(server_host="api.example.com", server_port=8080)
        assert s.api_base == "http://api.example.com:8080/api"

    def test_settings_from_env(self, monkeypatch):
        """Test loading settings from environment variables."""
        monkeypatch.setenv("COGNITION_SERVER_HOST", "remote.server.com")
        monkeypatch.setenv("COGNITION_SERVER_PORT", "9000")
        monkeypatch.setenv("COGNITION_USE_SSL", "true")
        monkeypatch.setenv("COGNITION_DEFAULT_NETWORK_MODE", "ON")

        s = ClientSettings()
        assert s.server_host == "remote.server.com"
        assert s.server_port == 9000
        assert s.use_ssl is True
        assert s.default_network_mode == "ON"

    def test_reconnect_settings(self):
        """Test reconnection configuration."""
        s = ClientSettings(
            ws_reconnect_attempts=10,
            ws_reconnect_delay=2.0,
        )
        assert s.ws_reconnect_attempts == 10
        assert s.ws_reconnect_delay == 2.0

    def test_heartbeat_interval(self):
        """Test heartbeat interval setting."""
        s = ClientSettings(ws_heartbeat_interval=60.0)
        assert s.ws_heartbeat_interval == 60.0

    def test_global_settings_instance(self):
        """Test that global settings instance is created."""
        assert isinstance(settings, ClientSettings)
        assert settings.server_host == "localhost"
        assert settings.server_port == 8000
