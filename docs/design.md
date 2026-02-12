# Cognition — Design Document

## What We're Building

A local AI coding assistant (like Claude Code / OpenCode) built on **LangGraph Deep Agents**. Client-server architecture for future PaaS support.

## Architecture

```
TUI Client (Textual)
    │ WebSocket
    ▼
FastAPI Server (port 8000)
    │ agent.astream_events()
    ▼
Deep Agent (create_deep_agent)
    │ tool calls
    ▼
Sandbox Backend (execute)
```

## Message Flow

1. User types message in TUI, sent over WebSocket as JSON
2. Server receives message, looks up session → `thread_id`
3. Server calls `agent.astream_events({"messages": [HumanMessage(content)]}, config={"configurable": {"thread_id": thread_id}})`
4. Deep Agent runs its loop (LLM → tool calls → LLM → ... → final response)
5. Server relays each streamed event back over WebSocket
6. TUI renders events in real-time (tool calls, tokens, results)

## Deep Agents — What We Get for Free

The `deepagents` library provides all of the following with zero custom code:

- **File tools**: `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`
- **Shell execution**: `execute` (via sandbox backends)
- **Task planning**: `write_todos`
- **Subagent spawning**: `task` (isolated context)
- **Context management**: Automatic eviction of large tool results to filesystem
- **Conversation summarization**: Triggers at 85% context window usage
- **Dangling tool call repair**: Fixes interrupted tool calls
- **Prompt caching**: For Anthropic models
- **Streaming**: Token-level + tool-level events
- **Checkpointing**: Full state persistence via `thread_id`

## What We Build

### 1. `LocalSandbox` (~30 lines)

Implements `SandboxBackendProtocol`. Runs commands on the host via `subprocess.run()`. `BaseSandbox` derives all filesystem tools from `execute()`.

```python
class LocalSandbox(BaseSandbox):
    def __init__(self, root_dir: str):
        self.root_dir = root_dir

    def execute(self, command: str) -> ExecuteResult:
        result = subprocess.run(
            command, shell=True, capture_output=True,
            text=True, cwd=self.root_dir, timeout=300,
        )
        return ExecuteResult(
            output=result.stdout + result.stderr,
            exit_code=result.returncode,
        )
```

### 2. Agent Factory (~20 lines)

```python
def create_cognition_agent(project_path: str, store=None):
    backend = lambda rt: CompositeBackend(
        default=LocalSandbox(root_dir=project_path),
        routes={
            "/memories/": StoreBackend(rt),
        },
    )
    return create_deep_agent(
        system_prompt=SYSTEM_PROMPT,
        backend=backend,
        store=store,
    )
```

### 3. FastAPI Server

WebSocket endpoint that:
- Accepts connections from TUI client
- Creates sessions (agent instance + thread_id)
- Receives user messages
- Calls `agent.astream_events()` and relays events over WebSocket

### 4. Textual TUI Client

Connects to server via WebSocket. Sends user messages. Renders streamed events:
- LLM tokens → append to message display
- Tool calls → show in collapsible block
- Tool results → show output
- Done → re-enable input

### 5. Session Management

Maps `session_id` → `thread_id` for LangGraph checkpointing. State persists automatically — disconnect and reconnect resumes the conversation.

### 6. Settings

Environment-based config for:
- LLM provider (`openai` or `bedrock`)
- API keys
- Default model
- Sandbox type
- OTEL endpoint

## Sandbox Backend Strategy

The sandbox backend is the **only** component that changes across deployment targets. Everything else — agent code, server code, TUI code — stays identical.

### Local Development

```python
backend = LocalSandbox(root_dir="./my-project")
# execute() → subprocess.run() on host
```

### Docker (Self-Hosted)

```python
class DockerSandbox(BaseSandbox):
    def __init__(self, image: str, workspace: str):
        self.client = docker.from_env()
        self.container = self.client.containers.run(
            image, detach=True,
            volumes={workspace: {"bind": "/workspace", "mode": "rw"}},
            working_dir="/workspace",
        )

    def execute(self, command: str) -> ExecuteResult:
        exit_code, output = self.container.exec_run(["bash", "-c", command])
        return ExecuteResult(output=output.decode(), exit_code=exit_code)
```

### Kubernetes (PaaS)

```python
class K8sSandbox(BaseSandbox):
    def __init__(self, namespace: str, pod_name: str):
        self.k8s = kubernetes.client.CoreV1Api()
        self.namespace = namespace
        self.pod_name = pod_name

    def execute(self, command: str) -> ExecuteResult:
        resp = kubernetes.stream.stream(
            self.k8s.connect_get_namespaced_pod_exec,
            self.pod_name, self.namespace,
            command=["bash", "-c", command],
            stdout=True, stderr=True,
        )
        return ExecuteResult(output=resp, exit_code=0)
```

### Comparison

| | Local | Docker | K8s |
|---|---|---|---|
| `execute()` via | `subprocess.run()` | `docker exec` | `kubectl exec` |
| Isolation | None (trusted) | Container | Pod + RBAC + NetworkPolicy |
| Filesystem | Host disk | Mounted volume | PVC |
| Agent code changes | None | None | None |
| Config | `SANDBOX_TYPE=local` | `SANDBOX_TYPE=docker` | `SANDBOX_TYPE=k8s` |

## Backend Routing (CompositeBackend)

| Path | Backend | Purpose |
|------|---------|---------|
| Default (`/workspace/`, etc.) | `LocalSandbox` / `DockerSandbox` / `K8sSandbox` | Agent reads/edits code, runs commands |
| `/memories/` | `StoreBackend` | Persistent cross-session knowledge |

## File Structure

```
cognition/
├── pyproject.toml
├── .env
├── server/
│   └── app/
│       ├── __init__.py
│       ├── main.py          # FastAPI + WebSocket endpoint
│       ├── settings.py      # Environment config
│       ├── sandbox.py       # LocalSandbox (+ future Docker/K8s)
│       ├── agent.py         # create_cognition_agent()
│       └── sessions.py      # Session ↔ thread_id mapping
├── client/
│   └── tui/
│       ├── __init__.py
│       ├── app.py           # Textual App
│       ├── api.py           # REST client (health, projects)
│       └── screens/
│           └── chat.py      # Chat interface
├── shared/
│   └── protocol.py          # Pydantic models for WebSocket events
├── tests/
│   ├── test_sandbox.py
│   ├── test_agent.py
│   └── test_server.py
└── docs/
    └── design.md            # This file
```

## Dependencies

```
deepagents          # Deep Agents SDK (includes langgraph, langchain)
fastapi             # HTTP + WebSocket server
uvicorn             # ASGI server
textual             # TUI framework
httpx               # HTTP client (TUI → server)
websockets          # WebSocket client (TUI → server)
pydantic-settings   # Environment config
structlog           # Structured logging
opentelemetry-api   # OTEL tracing
```

LLM providers (install as needed):
```
langchain-openai       # OpenAI + OpenAI-compatible
langchain-aws          # AWS Bedrock
```

## LLM Configuration

Deep Agents uses LangChain's model abstraction. We pass the model at agent creation:

```python
from langchain_openai import ChatOpenAI
from langchain_aws import ChatBedrock

# OpenAI
model = ChatOpenAI(model="gpt-4o")

# Bedrock
model = ChatBedrock(model_id="anthropic.claude-3-sonnet-20240229-v1:0")

agent = create_deep_agent(model=model, ...)
```

## OTEL Observability

LangGraph and LangChain emit spans natively when OTEL is configured. Set environment variables:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_SERVICE_NAME=cognition
```

Alternatively, use LangSmith for tracing:

```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=ls-...
```

## Security Model

| Deployment | Trust Level | Protections |
|-----------|------------|-------------|
| Local | Trusted user | None needed (same as Claude Code) |
| Docker | Trusted user, isolated env | Container boundary, volume mounts |
| K8s PaaS | Untrusted users | RBAC, NetworkPolicy, seccomp, resource limits, no secrets in sandbox |

## Key Design Decisions

1. **Client-server split** — Keeps the door open for PaaS. TUI is a thin WebSocket client.
2. **Deep Agents over custom agent loop** — Gets us file tools, context management, subagents, todos, summarization for free.
3. **Sandbox backend abstraction** — One interface, three implementations (local/docker/k8s). Agent code never changes.
4. **CompositeBackend for routing** — Separates ephemeral workspace from persistent memories.
5. **`astream_events` for streaming** — Granular token + tool events over WebSocket for responsive TUI.
6. **Checkpointing via `thread_id`** — Session persistence with zero custom code.

## Testing Strategy

### Testing Philosophy

Cognition uses a layered testing approach:
- **Unit tests**: Fast, isolated, no LLM calls (mocked)
- **Integration tests**: Test component interactions with real dependencies
- **E2E tests**: Full user workflow with configurable LLM access (can mock or use real)

All tests use `pytest` with `pytest-asyncio` for async code.

### Unit Tests

#### 1. Sandbox Tests (`tests/unit/test_sandbox.py`)

Test the `LocalSandbox` backend without any agent or LLM involvement:

```python
import pytest
import tempfile
import os
from pathlib import Path
from server.app.sandbox import LocalSandbox

class TestLocalSandbox:
    @pytest.fixture
    def sandbox(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield LocalSandbox(root_dir=tmpdir)

    def test_execute_echo(self, sandbox):
        """Test basic command execution."""
        result = sandbox.execute("echo 'hello world'")
        assert result.exit_code == 0
        assert "hello world" in result.output

    def test_execute_cwd(self, sandbox):
        """Test commands run in the correct working directory."""
        sandbox.execute("pwd")
        result = sandbox.execute("pwd")
        # Should output the sandbox root_dir
        assert sandbox.root_dir in result.output

    def test_execute_timeout(self, sandbox):
        """Test timeout enforcement."""
        result = sandbox.execute("sleep 10", timeout=0.1)
        assert result.exit_code != 0
        assert "timeout" in result.output.lower()

    def test_execute_failure(self, sandbox):
        """Test non-zero exit codes."""
        result = sandbox.execute("exit 42")
        assert result.exit_code == 42

    def test_path_traversal_blocked(self, sandbox):
        """Test that .. is blocked for security."""
        # Create a file outside the sandbox
        outside_file = Path(sandbox.root_dir).parent / "outside.txt"
        outside_file.write_text("secret")

        # Should not be able to read it
        result = sandbox.execute("cat ../outside.txt")
        assert "Permission denied" in result.output or result.exit_code != 0
```

#### 2. Session Management Tests (`tests/unit/test_sessions.py`)

Test session lifecycle without WebSocket or agent:

```python
import pytest
from unittest.mock import Mock, patch
from server.app.sessions import SessionManager
from server.app.settings import Settings

class TestSessionManager:
    @pytest.fixture
    def settings(self):
        return Settings(
            llm_provider="openai",
            openai_api_key="test-key",
        )

    @pytest.fixture
    def session_manager(self, settings):
        return SessionManager(settings=settings)

    def test_create_session(self, session_manager):
        """Test session creation."""
        session = session_manager.create_session(
            project_path="/tmp/test-project"
        )
        assert session.session_id is not None
        assert session.thread_id is not None
        assert session.project_path == "/tmp/test-project"

    def test_get_session(self, session_manager):
        """Test session retrieval."""
        created = session_manager.create_session(project_path="/tmp/test")
        retrieved = session_manager.get_session(created.session_id)
        assert retrieved.session_id == created.session_id

    def test_session_not_found(self, session_manager):
        """Test retrieving non-existent session."""
        with pytest.raises(SessionNotFoundError):
            session_manager.get_session("non-existent-id")

    def test_delete_session(self, session_manager):
        """Test session deletion."""
        session = session_manager.create_session(project_path="/tmp/test")
        session_manager.delete_session(session.session_id)
        
        with pytest.raises(SessionNotFoundError):
            session_manager.get_session(session.session_id)

    @pytest.mark.asyncio
    async def test_session_cleanup_on_timeout(self, session_manager):
        """Test that old sessions are cleaned up."""
        session = session_manager.create_session(
            project_path="/tmp/test",
            max_age_seconds=0.1
        )
        
        # Wait for timeout
        await asyncio.sleep(0.2)
        
        # Cleanup should remove it
        await session_manager.cleanup_expired()
        
        with pytest.raises(SessionNotFoundError):
            session_manager.get_session(session.session_id)
```

#### 3. Protocol Tests (`tests/unit/test_protocol.py`)

Test message serialization/deserialization:

```python
import pytest
from shared.protocol import (
    UserMessage,
    AgentChunk,
    ToolCall,
    ToolResult,
    SessionStarted,
)

class TestProtocol:
    def test_user_message_serialization(self):
        """Test UserMessage to JSON and back."""
        msg = UserMessage(
            session_id="sess-123",
            content="Hello agent",
            timestamp="2024-01-15T10:30:00Z"
        )
        json_str = msg.to_json()
        restored = UserMessage.from_json(json_str)
        
        assert restored.session_id == msg.session_id
        assert restored.content == msg.content

    def test_agent_chunk_streaming(self):
        """Test AgentChunk for streaming tokens."""
        chunks = [
            AgentChunk(type="token", content="Hello"),
            AgentChunk(type="token", content=" world"),
            AgentChunk(type="done", content=""),
        ]
        
        # Simulate streaming
        full_message = ""
        for chunk in chunks:
            if chunk.type == "token":
                full_message += chunk.content
        
        assert full_message == "Hello world"

    def test_tool_call_serialization(self):
        """Test ToolCall event."""
        tool_call = ToolCall(
            name="read_file",
            args={"path": "/workspace/main.py"},
            id="call-123"
        )
        json_str = tool_call.to_json()
        restored = ToolCall.from_json(json_str)
        
        assert restored.name == "read_file"
        assert restored.args["path"] == "/workspace/main.py"
```

### Integration Tests

#### 1. Agent + Sandbox Integration (`tests/integration/test_agent_sandbox.py`)

Test agent actually using sandbox (mocked LLM):

```python
import pytest
from unittest.mock import Mock, AsyncMock, patch
from server.app.agent import create_cognition_agent
from server.app.sandbox import LocalSandbox
import tempfile

class TestAgentSandboxIntegration:
    @pytest.fixture
    async def agent_with_mocked_llm(self):
        """Create agent with mocked LLM that returns predictable tool calls."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = create_cognition_agent(project_path=tmpdir)
            
            # Mock the LLM to simulate agent behavior
            mock_llm = Mock()
            mock_llm.ainvoke = AsyncMock(return_value=Mock(
                content="",
                tool_calls=[{
                    "name": "read_file",
                    "args": {"path": "/workspace/test.txt"},
                    "id": "call-1"
                }]
            ))
            agent.llm = mock_llm
            
            yield agent, tmpdir

    @pytest.mark.asyncio
    async def test_agent_reads_file_via_sandbox(self, agent_with_mocked_llm):
        """Test agent uses sandbox to read files."""
        agent, tmpdir = agent_with_mocked_llm
        
        # Create a file in the sandbox
        test_file = Path(tmpdir) / "test.txt"
        test_file.write_text("Hello from sandbox")
        
        # Run agent
        result = await agent.ainvoke({
            "messages": [{"role": "user", "content": "Read test.txt"}]
        })
        
        # Verify LLM was called with tool result
        assert mock_llm.ainvoke.called
        # The agent should have received the file content in its context

    @pytest.mark.asyncio
    async def test_agent_writes_file_via_sandbox(self, agent_with_mocked_llm):
        """Test agent uses sandbox to write files."""
        agent, tmpdir = agent_with_mocked_llm
        
        # Mock LLM to return a write_file tool call
        agent.llm.ainvoke = AsyncMock(return_value=Mock(
            content="",
            tool_calls=[{
                "name": "write_file",
                "args": {
                    "path": "/workspace/output.txt",
                    "content": "Created by agent"
                },
                "id": "call-1"
            }]
        ))
        
        await agent.ainvoke({
            "messages": [{"role": "user", "content": "Create a file"}]
        })
        
        # Verify file was created
        output_file = Path(tmpdir) / "output.txt"
        assert output_file.exists()
        assert output_file.read_text() == "Created by agent"
```

#### 2. Server + Agent Integration (`tests/integration/test_server_agent.py`)

Test FastAPI server with real agent but mocked LLM:

```python
import pytest
from fastapi.testclient import TestClient
from server.app.main import app
from server.app.sessions import get_session_manager

class TestServerAgentIntegration:
    @pytest.fixture
    def client(self):
        """Create test client with mocked dependencies."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Override settings for testing
            with patch("server.app.main.get_settings") as mock_settings:
                mock_settings.return_value = Settings(
                    llm_provider="openai",
                    openai_api_key="test-key-mocked",
                )
                
                # Mock the LLM to avoid actual API calls
                with patch("server.app.agent.ChatOpenAI") as mock_llm_class:
                    mock_llm = Mock()
                    mock_llm.ainvoke = AsyncMock(return_value=Mock(
                        content="I can help you with that!",
                        tool_calls=[]
                    ))
                    mock_llm_class.return_value = mock_llm
                    
                    yield TestClient(app)

    def test_health_endpoint(self, client):
        """Test health check."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_create_session(self, client):
        """Test session creation endpoint."""
        response = client.post("/sessions", json={
            "project_path": "/tmp/test-project"
        })
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert "thread_id" in data

    def test_websocket_connection(self, client):
        """Test WebSocket connection."""
        # Create session first
        session_resp = client.post("/sessions", json={
            "project_path": "/tmp/test-project"
        })
        session_id = session_resp.json()["session_id"]
        
        # Connect via WebSocket
        with client.websocket_connect(f"/ws/{session_id}") as websocket:
            # Send message
            websocket.send_json({
                "type": "user_message",
                "content": "Hello"
            })
            
            # Receive response (mocked LLM should respond)
            response = websocket.receive_json()
            assert response["type"] in ["token", "assistant_message", "done"]
```

### E2E Tests

#### Configurable LLM Access

E2E tests support three modes via environment variables:

```bash
# Mode 1: Fully mocked (default for CI)
COGNITION_TEST_LLM_MODE=mock

# Mode 2: Use OpenAI API (requires OPENAI_API_KEY)
COGNITION_TEST_LLM_MODE=openai

# Mode 3: Use local Ollama (requires ollama running)
COGNITION_TEST_LLM_MODE=ollama
COGNITION_TEST_OLLAMA_MODEL=llama3.2
```

#### E2E Test Implementation (`tests/e2e/test_full_workflow.py`)

```python
import pytest
import os
import tempfile
import subprocess
import time
import signal
from pathlib import Path

class TestE2EFullWorkflow:
    """End-to-end tests running actual server and TUI client.
    
    These tests start the real server process and connect via WebSocket.
    They can use mocked or real LLMs based on COGNITION_TEST_LLM_MODE.
    """

    @pytest.fixture(scope="module")
    def server_process(self):
        """Start the Cognition server as a subprocess."""
        # Create temp directory for workspaces
        with tempfile.TemporaryDirectory() as tmpdir:
            env = os.environ.copy()
            env["COGNITION_WORKSPACE_ROOT"] = tmpdir
            env["COGNITION_PORT"] = "8999"  # Non-standard port for testing
            
            # Configure LLM based on test mode
            llm_mode = os.getenv("COGNITION_TEST_LLM_MODE", "mock")
            
            if llm_mode == "mock":
                env["COGNITION_LLM_MODE"] = "mock"
                # Use fake API key for mocked mode
                env["OPENAI_API_KEY"] = "test-key-fake"
            elif llm_mode == "openai":
                env["COGNITION_LLM_MODE"] = "openai"
                # Real API key must be set in environment
                if not os.getenv("OPENAI_API_KEY"):
                    pytest.skip("OPENAI_API_KEY not set for E2E tests")
            elif llm_mode == "ollama":
                env["COGNITION_LLM_MODE"] = "ollama"
                env["COGNITION_OLLAMA_MODEL"] = os.getenv(
                    "COGNITION_TEST_OLLAMA_MODEL", "llama3.2"
                )
                # Check if ollama is running
                try:
                    subprocess.run(["ollama", "list"], check=True, capture_output=True)
                except (subprocess.CalledProcessError, FileNotFoundError):
                    pytest.skip("Ollama not available for E2E tests")
            
            # Start server
            proc = subprocess.Popen(
                ["python", "-m", "uvicorn", "server.app.main:app", 
                 "--port", "8999", "--host", "127.0.0.1"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            
            # Wait for server to be ready
            time.sleep(2)
            
            yield proc
            
            # Cleanup
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)

    @pytest.fixture
    def websocket_client(self, server_process):
        """Create WebSocket client connected to test server."""
        import websockets
        import asyncio
        
        async def connect():
            ws = await websockets.connect("ws://127.0.0.1:8999/ws")
            return ws
        
        return asyncio.get_event_loop().run_until_complete(connect())

    @pytest.mark.asyncio
    async def test_e2e_create_project_and_chat(self, server_process):
        """Full workflow: create project, start session, send message, get response."""
        import websockets
        import json
        
        async with websockets.connect("ws://127.0.0.1:8999/ws") as ws:
            # 1. Create project
            ws.send(json.dumps({
                "type": "create_project",
                "user_prefix": "e2e-test-project"
            }))
            
            response = json.loads(await ws.recv())
            assert response["type"] == "project_created"
            project_id = response["project_id"]
            
            # 2. Start session
            ws.send(json.dumps({
                "type": "create_session",
                "project_id": project_id
            }))
            
            response = json.loads(await ws.recv())
            assert response["type"] == "session_started"
            session_id = response["session_id"]
            
            # 3. Send message to agent
            ws.send(json.dumps({
                "type": "user_message",
                "session_id": session_id,
                "content": "Create a file called hello.txt with 'Hello World' inside"
            }))
            
            # 4. Collect all events until done
            events = []
            while True:
                event = json.loads(await ws.recv())
                events.append(event)
                
                if event["type"] == "done":
                    break
                
                # Timeout protection
                if len(events) > 1000:
                    pytest.fail("Too many events, possible infinite loop")
            
            # 5. Verify we got expected events
            event_types = [e["type"] for e in events]
            
            # Should see tool calls and responses
            assert "tool_call" in event_types, "Agent should have used a tool"
            assert "tool_result" in event_types, "Tool should have returned result"
            
            # Find the write_file tool call
            write_file_calls = [
                e for e in events 
                if e.get("name") == "write_file"
            ]
            assert len(write_file_calls) > 0, "Agent should call write_file"

    @pytest.mark.asyncio
    async def test_e2e_agent_reads_and_responds(self, server_process):
        """Test agent can read existing files and respond."""
        import websockets
        import json
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file for agent to read
            test_file = Path(tmpdir) / "test.py"
            test_file.write_text("def hello():\n    return 'world'")
            
            async with websockets.connect("ws://127.0.0.1:8999/ws") as ws:
                # Create project pointing to temp dir
                ws.send(json.dumps({
                    "type": "create_project",
                    "user_prefix": "e2e-read-test",
                    "project_path": str(tmpdir)
                }))
                
                response = json.loads(await ws.recv())
                project_id = response["project_id"]
                
                # Start session
                ws.send(json.dumps({
                    "type": "create_session",
                    "project_id": project_id
                }))
                
                response = json.loads(await ws.recv())
                session_id = response["session_id"]
                
                # Ask agent to read the file
                ws.send(json.dumps({
                    "type": "user_message",
                    "session_id": session_id,
                    "content": "What does the hello() function in test.py return?"
                }))
                
                # Collect events
                events = []
                while True:
                    event = json.loads(await ws.recv())
                    events.append(event)
                    if event["type"] == "done":
                        break
                
                # Verify agent read the file
                read_file_calls = [
                    e for e in events
                    if e.get("name") == "read_file" and 
                    "test.py" in str(e.get("args", {}).get("path", ""))
                ]
                assert len(read_file_calls) > 0, "Agent should read test.py"

    @pytest.mark.asyncio
    async def test_e2e_session_persistence(self, server_process):
        """Test that sessions persist across reconnections."""
        import websockets
        import json
        
        async with websockets.connect("ws://127.0.0.1:8999/ws") as ws:
            # Create project and session
            ws.send(json.dumps({
                "type": "create_project",
                "user_prefix": "e2e-persist-test"
            }))
            
            response = json.loads(await ws.recv())
            project_id = response["project_id"]
            
            ws.send(json.dumps({
                "type": "create_session",
                "project_id": project_id
            }))
            
            response = json.loads(await ws.recv())
            session_id = response["session_id"]
            thread_id = response["thread_id"]
            
            # Send first message
            ws.send(json.dumps({
                "type": "user_message",
                "session_id": session_id,
                "content": "My name is Alice"
            }))
            
            # Wait for completion
            while True:
                event = json.loads(await ws.recv())
                if event["type"] == "done":
                    break
        
        # Disconnect and reconnect
        async with websockets.connect("ws://127.0.0.1:8999/ws") as ws:
            # Resume session
            ws.send(json.dumps({
                "type": "resume_session",
                "session_id": session_id,
                "thread_id": thread_id
            }))
            
            # Ask about previous context
            ws.send(json.dumps({
                "type": "user_message",
                "session_id": session_id,
                "content": "What is my name?"
            }))
            
            # Collect response
            events = []
            while True:
                event = json.loads(await ws.recv())
                events.append(event)
                if event["type"] == "done":
                    break
            
            # Verify agent remembers
            response_content = " ".join([
                e.get("content", "") 
                for e in events 
                if e["type"] == "token"
            ])
            assert "Alice" in response_content, "Agent should remember context"
```

### LLM Configuration for Tests

#### Mock Mode (Default)

Uses a mock LLM that returns predictable responses:

```python
# server/app/llm/mock.py
class MockLLM:
    """Mock LLM for testing that simulates agent behavior."""
    
    async def ainvoke(self, messages, **kwargs):
        last_message = messages[-1]["content"]
        
        # Simulate understanding file operations
        if "create" in last_message.lower() and "file" in last_message.lower():
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "write_file",
                    "args": {"path": "/workspace/hello.txt", "content": "Hello World"},
                    "id": "mock-call-1"
                }]
            )
        
        if "read" in last_message.lower():
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "read_file",
                    "args": {"path": "/workspace/test.txt"},
                    "id": "mock-call-2"
                }]
            )
        
        return AIMessage(content="I understand. Let me help you with that.")
```

#### Running E2E Tests

```bash
# Run with mocked LLM (fast, no API calls)
COGNITION_TEST_LLM_MODE=mock pytest tests/e2e/ -v

# Run with real OpenAI (requires API key)
export OPENAI_API_KEY="sk-..."
COGNITION_TEST_LLM_MODE=openai pytest tests/e2e/ -v --timeout=120

# Run with local Ollama
COGNITION_TEST_LLM_MODE=ollama \
COGNITION_TEST_OLLAMA_MODEL=llama3.2 \
pytest tests/e2e/ -v --timeout=300

# Run specific test
pytest tests/e2e/test_full_workflow.py::TestE2EFullWorkflow::test_e2e_create_project_and_chat -v
```

### CI/CD Integration

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      
      - name: Run unit tests
        run: uv run pytest tests/unit/ -v --cov=server --cov=client

  integration-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      
      - name: Run integration tests
        run: uv run pytest tests/integration/ -v
        env:
          COGNITION_TEST_LLM_MODE: mock

  e2e-tests-mocked:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      
      - name: Run E2E tests (mocked)
        run: uv run pytest tests/e2e/ -v --timeout=60
        env:
          COGNITION_TEST_LLM_MODE: mock

  e2e-tests-real:
    runs-on: ubuntu-latest
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      
      - name: Run E2E tests (real OpenAI)
        run: uv run pytest tests/e2e/ -v --timeout=300
        env:
          COGNITION_TEST_LLM_MODE: openai
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY_E2E }}
```

### Test Utilities

```python
# tests/utils.py
import asyncio
import json
from contextlib import asynccontextmanager

@asynccontextmanager
async def managed_session(server_url: str, project_path: str = None):
    """Context manager for test sessions."""
    import websockets
    
    ws = await websockets.connect(f"{server_url}/ws")
    
    # Create project
    if project_path:
        await ws.send(json.dumps({
            "type": "create_project",
            "project_path": project_path
        }))
    else:
        await ws.send(json.dumps({
            "type": "create_project",
            "user_prefix": "test-" + str(uuid.uuid4())[:8]
        }))
    
    response = json.loads(await ws.recv())
    project_id = response["project_id"]
    
    # Create session
    await ws.send(json.dumps({
        "type": "create_session",
        "project_id": project_id
    }))
    
    response = json.loads(await ws.recv())
    session_id = response["session_id"]
    
    try:
        yield ws, session_id
    finally:
        await ws.close()

async def collect_agent_response(ws, timeout=60):
    """Helper to collect all events until done."""
    events = []
    start = asyncio.get_event_loop().time()
    
    while True:
        if asyncio.get_event_loop().time() - start > timeout:
            raise TimeoutError("Agent response timeout")
        
        event = json.loads(await ws.recv())
        events.append(event)
        
        if event["type"] == "done":
            break
    
    return events
```

## References

- Deep Agents docs: https://docs.langchain.com/oss/python/deepagents/overview
- Deep Agents backends: https://docs.langchain.com/oss/python/deepagents/backends
- Deep Agents sandboxes: https://docs.langchain.com/oss/python/deepagents/sandboxes
- Deep Agents harness: https://docs.langchain.com/oss/python/deepagents/harness
- LangGraph streaming: https://docs.langchain.com/oss/python/langgraph/how-tos
- pytest-asyncio: https://pytest-asyncio.readthedocs.io/
- pytest-timeout: https://pypi.org/project/pytest-timeout/
