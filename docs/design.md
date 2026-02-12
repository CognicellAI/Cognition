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

## References

- Deep Agents docs: https://docs.langchain.com/oss/python/deepagents/overview
- Deep Agents backends: https://docs.langchain.com/oss/python/deepagents/backends
- Deep Agents sandboxes: https://docs.langchain.com/oss/python/deepagents/sandboxes
- Deep Agents harness: https://docs.langchain.com/oss/python/deepagents/harness
- LangGraph streaming: https://docs.langchain.com/oss/python/langgraph/how-tos
