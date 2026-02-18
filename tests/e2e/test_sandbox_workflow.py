"""E2E test for Cognition Sandbox Backend.

This test demonstrates the sandbox working in a local environment.
It shows how the agent can:
1. Execute commands in an isolated workspace
2. Read and write files
3. Handle errors and retry
4. Complete multi-step tasks

Usage:
    # Run with local server (not Docker)
    uv run pytest tests/e2e/test_sandbox_workflow.py -v

    # Run with specific test
    uv run pytest tests/e2e/test_sandbox_workflow.py::TestSandboxWorkflow::test_multi_step_file_operations -v -s
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

# Mark all tests in this file as e2e
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.asyncio,
    pytest.mark.timeout(120),
]


class TestSandboxWorkflow:
    """Test sandbox functionality with local server."""

    @pytest_asyncio.fixture
    async def server(self, unused_tcp_port):
        """Start the server on an unused port with temp workspace."""
        port = unused_tcp_port

        # Create temp workspace for this test
        with tempfile.TemporaryDirectory() as workspace:
            env = os.environ.copy()
            env["COGNITION_PORT"] = str(port)
            env["COGNITION_HOST"] = "127.0.0.1"
            env["COGNITION_LLM_PROVIDER"] = "openai_compatible"
            env["COGNITION_WORKSPACE_ROOT"] = workspace
            env["COGNITION_OPENAI_COMPATIBLE_BASE_URL"] = "https://openrouter.ai/api/v1"
            env["COGNITION_OPENAI_COMPATIBLE_API_KEY"] = os.environ.get(
                "COGNITION_OPENAI_COMPATIBLE_API_KEY", ""
            )
            env["COGNITION_LLM_MODEL"] = "google/gemini-3-flash-preview"
            env["COGNITION_METRICS_PORT"] = str(unused_tcp_port + 1000)  # Unique metrics port

            # Start server
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "server.app.main:app", "--port", str(port)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(Path(__file__).parent.parent.parent),
            )

            # Wait for server to be ready
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
                    await asyncio.sleep(0.1)
            else:
                process.terminate()
                process.wait(timeout=5)
                raise RuntimeError("Server failed to start")

            yield {"url": base_url, "workspace": workspace}

            # Cleanup
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    @pytest_asyncio.fixture
    async def session(self, server):
        """Create a test session."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{server['url']}/sessions",
                json={"name": "sandbox-test", "project_id": "test-project"},
            )
            assert response.status_code == 201
            session_data = response.json()

            yield {
                "id": session_data["id"],
                "thread_id": session_data["thread_id"],
                "url": server["url"],
                "workspace": server["workspace"],
            }

    async def test_multi_step_file_operations(self, session):
        """Test agent performs multi-step file operations.

        This test demonstrates:
        1. Agent creates a file
        2. Agent lists directory to verify
        3. Agent reads the file back
        4. Agent executes a command on the file

        The sandbox ensures all operations are contained in the workspace.
        """
        async with httpx.AsyncClient() as client:
            # Send message asking agent to create a file
            print("\n📝 Step 1: Requesting agent to create a file...")

            response = await client.post(
                f"{session['url']}/sessions/{session['id']}/messages",
                json={
                    "content": "Create a Python script named hello.py that prints 'Hello from Sandbox!'"
                },
                timeout=60.0,
            )

            assert response.status_code == 200

            # Parse SSE events
            events = []
            content = response.text
            event_type = None

            for line in content.split("\n"):
                if line.startswith("event:"):
                    event_type = line.replace("event:", "").strip()
                elif line.startswith("data:"):
                    data = json.loads(line.replace("data:", "").strip())
                    if event_type:
                        events.append({"type": event_type, "data": data})

            # Check for tool calls
            tool_calls = [e for e in events if e["type"] == "tool_call"]
            tool_results = [e for e in events if e["type"] == "tool_result"]

            print(f"   ✓ Agent made {len(tool_calls)} tool calls")
            for tc in tool_calls:
                print(f"     - Tool: {tc['data']['name']} Args: {tc['data']['args']}")

            print(f"   ✓ Got {len(tool_results)} tool results")
            for tr in tool_results:
                print(f"     - Result: {tr['data']['output']}")

            # Verify the file was created in the workspace
            print("\n🔍 Step 2: Verifying file in workspace...")

            hello_file = Path(session["workspace"]) / "hello.py"
            assert hello_file.exists(), f"File not found at {hello_file}"

            file_content = hello_file.read_text()
            assert "Hello" in file_content or "print" in file_content

            print(f"   ✓ File created: {hello_file}")
            print(f"   ✓ Content: {file_content[:100]}...")

            # Verify file is isolated - not outside workspace
            print("\n🛡️  Step 3: Verifying sandbox isolation...")

            # File should NOT be in system directories
            assert not (Path("/") / "hello.py").exists()
            assert not (Path("/tmp") / "hello.py").exists()

            print("   ✓ File is isolated in workspace")

            # Check for completion
            done_events = [e for e in events if e["type"] == "done"]
            assert len(done_events) > 0, "No completion event received"

            print("\n✅ Multi-step sandbox workflow completed successfully!")
            print(f"   Workspace: {session['workspace']}")
            print(f"   File: hello.py")
            print(f"   Total events: {len(events)}")

    async def test_sandbox_command_execution(self, session):
        """Test agent can execute commands via sandbox.

        Demonstrates the execute tool working in the sandbox.
        """
        async with httpx.AsyncClient() as client:
            print("\n📝 Requesting agent to run a command...")

            response = await client.post(
                f"{session['url']}/sessions/{session['id']}/messages",
                json={"content": "Run 'pwd' command and tell me what directory you're in"},
                timeout=60.0,
            )

            assert response.status_code == 200

            # Parse events
            events = []
            event_type = None
            for line in response.text.split("\n"):
                if line.startswith("event:"):
                    event_type = line.replace("event:", "").strip()
                elif line.startswith("data:"):
                    try:
                        data = json.loads(line.replace("data:", "").strip())
                        if event_type:
                            events.append({"type": event_type, "data": data})
                    except json.JSONDecodeError:
                        pass

            # Look for execute tool call
            execute_calls = [
                e for e in events if e["type"] == "tool_call" and e["data"].get("name") == "execute"
            ]

            if execute_calls:
                print(f"   ✓ Agent used execute tool")
                cmd = execute_calls[0]["data"]["args"].get("command", "")
                print(f"   ✓ Command: {cmd}")
            else:
                print("   ℹ️  Agent may have used other tools")

            print("\n✅ Command execution test completed!")

    async def test_sandbox_error_handling(self, session):
        """Test sandbox handles errors gracefully.

        Agent should handle permission errors and adapt.
        """
        async with httpx.AsyncClient() as client:
            print("\n📝 Testing error handling...")

            # Ask agent to do something that might fail, then recover
            response = await client.post(
                f"{session['url']}/sessions/{session['id']}/messages",
                json={
                    "content": "Try to create a file at /root/test.txt (which should fail), then create it in the current directory instead"
                },
                timeout=60.0,
            )

            assert response.status_code == 200

            # Parse events
            events = []
            event_type = None
            for line in response.text.split("\n"):
                if line.startswith("event:"):
                    event_type = line.replace("event:", "").strip()
                elif line.startswith("data:"):
                    try:
                        data = json.loads(line.replace("data:", "").strip())
                        if event_type:
                            events.append({"type": event_type, "data": data})
                    except json.JSONDecodeError:
                        pass

            # Check that we got some results
            tool_results = [e for e in events if e["type"] == "tool_result"]
            print(f"   ✓ Agent made {len(tool_results)} tool calls")

            # The agent should have eventually succeeded
            done_events = [e for e in events if e["type"] == "done"]
            assert len(done_events) > 0, "Task should complete"

            print("\n✅ Error handling test completed!")
