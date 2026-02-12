"""In-process agent runtime using LangGraph Deep Agents."""

from __future__ import annotations

from typing import Any, AsyncGenerator

import structlog
from langgraph.graph import StateGraph, MessagesState, START, END
from server.app.agent.llm_handler import LLMHandler
from server.app.settings import Settings

logger = structlog.get_logger()


class InProcessAgent:
    """In-process agent for handling user interactions with LLM.

    Built on LangGraph for state management and context window optimization.
    No Docker containers - runs in the same process as the server.
    """

    def __init__(self, settings: Settings, session_id: str):
        """Initialize agent.

        Args:
            settings: Server settings
            session_id: Session ID for logging and context
        """
        self.settings = settings
        self.session_id = session_id
        self.llm = LLMHandler(settings)
        self._build_graph()

    def _build_graph(self) -> None:
        """Build LangGraph state machine for agent logic."""
        builder = StateGraph(MessagesState)

        def process_user_input(state: MessagesState) -> MessagesState:
            """Process user input and prepare for LLM call."""
            # This is where context management happens
            # - Summarize old messages if needed (context window management)
            # - Inject system prompt
            # - Format messages for LLM
            return state

        async def call_llm(state: MessagesState) -> dict[str, Any]:
            """Call LLM with current state."""
            # Extract messages from state
            messages = [
                {"role": msg.type, "content": msg.content} for msg in (state.get("messages") or [])
            ]

            # Call LLM (no streaming for now, can add later)
            try:
                response = await self.llm.generate_response(
                    messages,
                    model=self.settings.default_model,
                    temperature=0.7,
                    max_tokens=4096,
                )
                return {"messages": [{"type": "assistant", "content": response}]}
            except Exception as e:
                logger.error("LLM call failed", session_id=self.session_id, error=str(e))
                return {"messages": [{"type": "assistant", "content": f"Error: {str(e)}"}]}

        # Add nodes
        builder.add_node("process", process_user_input)
        builder.add_node("llm", call_llm)

        # Add edges
        builder.add_edge(START, "process")
        builder.add_edge("process", "llm")
        builder.add_edge("llm", END)

        self.graph = builder.compile()

    async def process_message(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Process a user message and return agent response.

        Args:
            user_message: The user's input message
            context: Optional context (project info, etc)

        Returns:
            Agent's response
        """
        logger.info(
            "Processing message",
            session_id=self.session_id,
            message_length=len(user_message),
        )

        try:
            # For now, simple direct LLM call
            # Later: integrate with file reading tools, code analysis, etc.
            messages = [{"role": "user", "content": user_message}]
            response = await self.llm.generate_response(messages)
            return response
        except Exception as e:
            logger.error(
                "Message processing failed",
                session_id=self.session_id,
                error=str(e),
            )
            raise

    async def stream_message(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream response to a user message.

        Args:
            user_message: The user's input message
            context: Optional context

        Yields:
            Response chunks
        """
        logger.info(
            "Streaming message",
            session_id=self.session_id,
            message_length=len(user_message),
        )

        try:
            messages = [{"role": "user", "content": user_message}]
            async for chunk in self.llm.stream_response(messages):
                yield chunk
        except Exception as e:
            logger.error(
                "Stream failed",
                session_id=self.session_id,
                error=str(e),
            )
            raise
