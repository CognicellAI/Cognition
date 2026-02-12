"""Agent container entrypoint.

This module is the main entry point for the agent runtime inside the container.
It initializes the agent, starts the WebSocket server, and handles the lifecycle.

Usage:
    python -m agent.entrypoint

Environment Variables:
    AGENT_PORT: Port to listen on (default: 9000)
    AGENT_HOST: Host to bind to (default: 0.0.0.0)
    LOG_LEVEL: Logging level (default: info)
    OTEL_SERVICE_NAME: OpenTelemetry service name
    OTEL_EXPORTER_OTLP_ENDPOINT: OTLP endpoint for tracing
"""

from __future__ import annotations

import asyncio
import os
import sys

import structlog

from agent.runtime import AgentRuntimeServer


def setup_logging() -> None:
    """Configure structured logging for the agent runtime."""
    import logging

    log_level = os.environ.get("LOG_LEVEL", "info").upper()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level, logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


async def main() -> int:
    """Main entry point for the agent container."""
    setup_logging()
    logger = structlog.get_logger()

    host = os.environ.get("AGENT_HOST", "0.0.0.0")
    port = int(os.environ.get("AGENT_PORT", "9000"))

    logger.info(
        "Starting agent runtime server",
        host=host,
        port=port,
        pid=os.getpid(),
    )

    server = AgentRuntimeServer(host=host, port=port)

    try:
        await server.start()
        logger.info("Agent runtime server started, waiting for connections")

        # Keep running until shutdown signal
        await server.wait_for_shutdown()

        logger.info("Agent runtime server shutting down")
        await server.stop()
        return 0

    except Exception as e:
        logger.error("Agent runtime failed", error=str(e))
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
