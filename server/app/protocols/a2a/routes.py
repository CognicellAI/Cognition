"""A2A route mounting for Cognition's FastAPI application.

Uses the a2a-sdk route factories to create Starlette routes for
Agent Card discovery and JSON-RPC A2A methods, then mounts them
onto Cognition's existing FastAPI app at /a2a.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI

from server.app.protocols.a2a.card import build_agent_card
from server.app.protocols.a2a.executor import CognitionA2AExecutor

if TYPE_CHECKING:
    from server.app.llm.deep_agent_service import SessionAgentManager
    from server.app.settings import Settings
    from server.app.storage.backend import StorageBackend
    from server.app.storage.config_store import ConfigStore

logger = structlog.get_logger(__name__)


async def mount_a2a_routes(
    app: FastAPI,
    settings: Settings,
    config_store: ConfigStore,
    session_agent_manager: SessionAgentManager,
    store: StorageBackend,
    version: str,
) -> None:
    """Mount A2A protocol routes on the FastAPI app.

    Creates an Agent Card from configured agents, builds the executor
    bridge, and mounts Agent Card discovery + JSON-RPC routes at /a2a.
    """
    # Build Agent Card from configured agents
    agents = await config_store.list_agent_definitions()
    host = settings.host
    port = settings.port
    base_url = f"http://{host}:{port}"
    agent_card = build_agent_card(agents, base_url, version)

    # Create executor
    executor = CognitionA2AExecutor(
        settings=settings,
        session_agent_manager=session_agent_manager,
        store=store,
    )

    # Create request handler
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    # Mount Agent Card discovery routes
    for route in create_agent_card_routes(agent_card):
        app.routes.append(route)

    # Mount JSON-RPC A2A routes at /a2a
    for route in create_jsonrpc_routes(handler, "/a2a"):
        app.routes.append(route)

    logger.info(
        "A2A adapter mounted",
        endpoint="/a2a",
        agent_card_url="/.well-known/agent-card.json",
        skill_count=len(agent_card.skills),
    )
