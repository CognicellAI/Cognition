"""Mock LLM for testing."""

from __future__ import annotations

from typing import Any, AsyncGenerator, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)


class MockLLM(BaseChatModel):
    """Mock LLM for testing that simulates agent behavior.

    This mock responds with predictable tool calls based on message content,
    allowing tests to run without making actual LLM API calls.
    """

    @property
    def _llm_type(self) -> str:
        return "mock"

    @property
    def profile(self) -> Any:
        return {"max_input_tokens": 100000}

    def _generate(self, *args, **kwargs):
        raise NotImplementedError("Use ainvoke instead")

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:
        """Bind tools to this mock LLM.

        Args:
            tools: List of tools to bind.

        Returns:
            Self (tools are handled in ainvoke/astream).
        """
        # Just return self since we handle tools in the invoke/stream methods
        return self

    async def ainvoke(
        self,
        input: list[BaseMessage] | str,
        config: Any | None = None,
        **kwargs: Any,
    ) -> AIMessage:
        """Invoke the mock LLM.

        Analyzes the last message and returns appropriate tool calls
        or responses based on simple pattern matching.
        """
        messages = input if isinstance(input, list) else [HumanMessage(content=input)]
        last_message = str(messages[-1].content).lower()

        # Generic tool trigger for testing
        if "trigger tool" in last_message:
            import re

            match = re.search(r"trigger tool ([\w-]+)", last_message)
            if match:
                tool_name = match.group(1)
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": tool_name,
                            "args": {"arg": "value"},
                            "id": f"mock-call-{tool_name}",
                        }
                    ],
                )

        # Echo system prompt if requested
        if "what is in my system prompt" in last_message:
            system_msg = next(
                (str(m.content) for m in messages if isinstance(m, SystemMessage)), "None"
            )
            return AIMessage(content=f"System prompt contains: {system_msg}")

        # Simulate file creation
        if "create" in last_message and "file" in last_message:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {
                            "path": "/workspace/hello.txt",
                            "content": "Hello World",
                        },
                        "id": "mock-call-1",
                    }
                ],
            )

        # Simulate file reading
        if "read" in last_message:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"path": "/workspace/test.txt"},
                        "id": "mock-call-2",
                    }
                ],
            )

        # Simulate listing files
        if "list" in last_message or "ls" in last_message:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ls",
                        "args": {"path": "/workspace"},
                        "id": "mock-call-3",
                    }
                ],
            )

        # Simulate running tests
        if "test" in last_message or "pytest" in last_message:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "execute",
                        "args": {"command": "pytest tests/"},
                        "id": "mock-call-4",
                    }
                ],
            )

        # Default response
        return AIMessage(
            content="I understand. Let me help you with that.",
            tool_calls=[],
        )

    async def astream(
        self,
        input: list[BaseMessage] | str,
        config: Any | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[AIMessageChunk, None]:
        """Stream the mock LLM response.

        Yields chunks of the response for streaming with proper callback support.
        """
        from langchain_core.callbacks.manager import AsyncCallbackManager

        messages = input if isinstance(input, list) else [HumanMessage(content=input)]
        last_message = str(messages[-1].content).lower()
        response_text = "I understand. Let me help you with that."

        # Get callback manager from config
        callback_manager = None
        if config and hasattr(config, "get"):
            callbacks = config.get("callbacks")
            if callbacks:
                callback_manager = AsyncCallbackManager.configure(
                    callbacks,
                    self.callbacks,
                    self.verbose,
                )

        # Handle tool triggers in astream (important for E2E tests)
        if "trigger tool" in last_message:
            import re

            match = re.search(r"trigger tool ([\w-]+)", last_message)
            if match:
                tool_name = match.group(1)
                chunk = AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": tool_name,
                            "args": '{"arg": "value"}',
                            "id": f"mock-call-{tool_name}",
                            "index": 0,
                        }
                    ],
                )
                # Emit callback event if manager available
                if callback_manager:
                    await callback_manager.on_llm_new_token(
                        "",
                        chunk=chunk,
                    )
                yield chunk
                return

        # Echo system prompt if requested
        if "what is in my system prompt" in last_message:
            system_msg = next(
                (str(m.content) for m in messages if isinstance(m, SystemMessage)), "None"
            )
            response_text = f"System prompt contains: {system_msg}"

        # Simple pattern matching for different responses
        elif "hello" in last_message:
            response_text = "Hello! How can I assist you today?"
        elif "help" in last_message:
            response_text = "I'd be happy to help! What would you like to work on?"

        # Yield tokens word by word to simulate streaming with callbacks
        words = response_text.split()
        for i, word in enumerate(words):
            chunk_text = word + (" " if i < len(words) - 1 else "")
            chunk = AIMessageChunk(content=chunk_text)

            # Emit callback event for each token
            if callback_manager:
                await callback_manager.on_llm_new_token(
                    chunk_text,
                    chunk=chunk,
                )

            yield chunk

        # Yield final empty chunk to signal completion
        yield AIMessageChunk(content="")
