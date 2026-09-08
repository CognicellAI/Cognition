"""Deep Agents middleware for explicit sandbox-file publication."""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.tools import ToolRuntime, tool

from server.app.storage.artifact_store import S3ArtifactStore
from server.app.storage.published_file import publish_file
from server.app.telemetry import operation


class PublicationMiddleware(AgentMiddleware):
    """Expose publication only on a backend with bounded remote file reads."""

    def __init__(
        self,
        sandbox: Any,
        store: S3ArtifactStore,
        scope: dict[str, str],
        *,
        inline_limit: int = 256 * 1024,
        max_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        if not 0 <= inline_limit <= max_bytes <= 10 * 1024 * 1024:
            raise ValueError("Invalid artifact publication limits")
        expected_scope = dict(scope)

        @tool(response_format="content_and_artifact")
        async def publish_artifact(
            path: str, runtime: ToolRuntime, media_type: str = "application/octet-stream"
        ) -> tuple[str, Any]:
            """Publish a completed sandbox workspace file as a durable client deliverable.

            Supply its absolute sandbox path and MIME type. Deployment policy
            determines inline bytes or download links. Maximum size is 10 MiB.
            """
            with operation("cognition.publication.validate"):
                if runtime.context is None or runtime.context.effective_scope != expected_scope:
                    raise ValueError("Publication scope mismatch")
                if not path.startswith("/workspace/") or any(
                    x in {".", ".."} for x in path.split("/")
                ):
                    raise ValueError("Publication requires an absolute sandbox workspace path")
                if "\r" in media_type or "\n" in media_type:
                    raise ValueError("Invalid media type")
            with operation("cognition.publication.download"):
                body = await asyncio.to_thread(sandbox.download_file_bounded, path, max_bytes)
            result = await publish_file(
                store, expected_scope, body, PurePosixPath(path).name, media_type, inline_limit
            )
            # Bytes are returned only as an internal tool artifact, never model text.
            return f"Published {result.filename} ({result.size} bytes), artifact {result.id}.", {
                "published_file": result,
                "inline_body": body if result.kind == "raw" else None,
            }

        self.tools = [publish_artifact]
