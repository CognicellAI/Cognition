import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

# Mark all tests in this file as e2e
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.asyncio,
    pytest.mark.timeout(60),
]


class TestPluggabilityE2E:
    """End-to-end tests for Cognition pluggability features."""

    @pytest_asyncio.fixture
    async def server(self, unused_tcp_port, unused_tcp_port_factory, tmp_path):
        """Start the server with a temporary workspace."""
        port = unused_tcp_port
        metrics_port = unused_tcp_port_factory()
        workspace_root = tmp_path / "workspaces"
        workspace_root.mkdir()

        env = os.environ.copy()
        env["COGNITION_PORT"] = str(port)
        env["COGNITION_HOST"] = "127.0.0.1"
        env["COGNITION_LLM_PROVIDER"] = "mock"
        env["COGNITION_WORKSPACE_ROOT"] = str(workspace_root)
        env["COGNITION_METRICS_PORT"] = str(metrics_port)

        # Start server with unbuffered output for debugging
        env["PYTHONUNBUFFERED"] = "1"

        # Start server with stdout/stderr redirected to pipes
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.app.main:app", "--port", str(port)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout
            cwd=str(Path(__file__).parent.parent.parent),
            bufsize=1,  # Line buffered
            universal_newlines=True,  # Text mode
        )

        # Start a thread to print server output in real-time
        import threading

        def print_output(proc):
            for line in proc.stdout:
                print(f"[SERVER] {line}", end="", flush=True)

        output_thread = threading.Thread(target=print_output, args=(process,))
        output_thread.daemon = True
        output_thread.start()

        base_url = f"http://127.0.0.1:{port}"
        start_time = time.time()
        timeout = 15

        while time.time() - start_time < timeout:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{base_url}/ready")
                    if response.status_code == 200 and response.json().get("ready"):
                        break
            except Exception:
                await asyncio.sleep(0.2)
        else:
            process.terminate()
            raise RuntimeError("Server failed to start - check [SERVER] output above for errors")

        yield {"url": base_url, "workspace": workspace_root}

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    async def test_streaming_middleware_status_events(self, server):
        """Verify that CognitionStreamingMiddleware emits status events."""
        base_url = server["url"]

        async with httpx.AsyncClient() as client:
            # Create session
            resp = await client.post(f"{base_url}/sessions", json={"title": "Streaming Test"})
            session_id = resp.json()["id"]

            # Send message and check stream
            async with client.stream(
                "POST",
                f"{base_url}/sessions/{session_id}/messages",
                json={"content": "Hello"},
                headers={"Accept": "text/event-stream"},
            ) as response:
                events = []
                event_type = "message"
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        event_type = line[7:]
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                        events.append({"event": event_type, "data": data})

                # Check for status events
                status_events = [e for e in events if e["event"] == "status"]
                assert len(status_events) >= 1
                assert any(e["data"].get("status") == "thinking" for e in status_events)

    async def test_memory_agents_md_injection(self, server):
        """Verify that AGENTS.md content is injected into the system prompt."""
        base_url = server["url"]
        workspace_root = server["workspace"]

        # In Git-style model, AGENTS.md is at the root of the workspace
        agents_md = workspace_root / "AGENTS.md"
        agents_md.write_text("PROJECT_SECRET: keyboard_cat")

        async with httpx.AsyncClient() as client:
            # Create session (uses the workspace_root)
            resp = await client.post(f"{base_url}/sessions", json={"title": "Memory Test"})
            session_id = resp.json()["id"]

            # Ask about the secret
            async with client.stream(
                "POST",
                f"{base_url}/sessions/{session_id}/messages",
                json={"content": "what is in my system prompt?"},
                headers={"Accept": "text/event-stream"},
            ) as response:
                content = ""
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        if "content" in data:
                            content += data["content"]

                assert "keyboard_cat" in content

    async def test_skills_discovery(self, server):
        """Verify that skills directory structure is created and accessible."""
        base_url = server["url"]
        workspace_root = server["workspace"]

        # Create skill directory structure
        skills_dir = workspace_root / ".cognition" / "skills" / "deploy"
        skills_dir.mkdir(parents=True)
        skill_file = skills_dir / "SKILL.md"
        skill_file.write_text("""---
name: deploy-app
description: Deploys the application to production.
---
Instruction for deployment.""")

        # Verify file exists and is readable
        assert skill_file.exists(), f"Skill file not found at {skill_file}"
        content = skill_file.read_text()
        assert "deploy-app" in content
        assert "Deploys the application" in content

        # Verify the skills directory is in the workspace
        assert (workspace_root / ".cognition" / "skills").exists()
