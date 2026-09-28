from __future__ import annotations

from server.app.agent.mcp_transport import server_notification_stream_enabled


def test_server_notification_stream_is_enabled_by_default() -> None:
    assert server_notification_stream_enabled({"transport": "streamable_http"}) is True


def test_server_notification_stream_can_be_disabled_per_connection() -> None:
    assert (
        server_notification_stream_enabled(
            {"transport": "streamable_http", "server_notification_stream": False}
        )
        is False
    )
