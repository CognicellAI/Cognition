"""Mock LLM for testing."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage


class MockLLM:
    """Mock LLM for testing that simulates agent behavior.

    This mock responds with predictable tool calls based on message content,
    allowing tests to run without making actual LLM API calls.
    """

    def bind_tools(self, tools: list[Any]) -> "MockLLM":
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
        messages: list[BaseMessage],
        **kwargs: Any,
    ) -> AIMessage:
        """Invoke the mock LLM.

        Analyzes the last message and returns appropriate tool calls
        or responses based on simple pattern matching.
        """
        last_message = messages[-1].content

        # Simulate file creation
        if "create" in str(last_message).lower() and "file" in str(last_message).lower():
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
        if "read" in str(last_message).lower():
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
        if "list" in str(last_message).lower() or "ls" in str(last_message).lower():
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
        if "test" in str(last_message).lower() or "pytest" in str(last_message).lower():
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
        messages: list[BaseMessage],
        **kwargs: Any,
    ) -> AsyncGenerator[AIMessage, None]:
        """Stream the mock LLM response.

        Yields chunks of the response for streaming.
        """
        from langchain_core.messages import AIMessageChunk

        last_message = messages[-1].content
        response_text = "I understand. Let me help you with that."

        # Simple pattern matching for different responses
        if "hello" in str(last_message).lower():
            response_text = "Hello! How can I assist you today?"
        elif "help" in str(last_message).lower():
            response_text = "I'd be happy to help! What would you like to work on?"

        # Yield tokens word by word to simulate streaming
        words = response_text.split()
        for i, word in enumerate(words):
            chunk_text = word + (" " if i < len(words) - 1 else "")
            yield AIMessageChunk(content=chunk_text)

        # Yield final empty chunk to signal completion
        yield AIMessageChunk(content="")
