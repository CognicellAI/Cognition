# Cognition Architecture (Refined)

## Overview

Cognition is an OpenCode-style AI coding assistant built for **local execution with eventual PaaS support**. The architecture has been refactored to remove Docker container complexity and focus on in-process agent execution.

**Current Status**: MVP with in-process agents, multi-LLM support, persistent sessions ready.

---

## High-Level Architecture

```
┌──────────────────────────────────────────────────────────┐
│               TUI Client (Textual)                        │
│  • Project picker                                         │
│  • Chat interface                                         │
│  • Settings panel (future)                                │
└──────────────┬───────────────────────────────────────────┘
               │ REST API + WebSocket
               ↓
┌──────────────────────────────────────────────────────────┐
│              FastAPI Server (Port 8000)                   │
│  • /api/projects - CRUD operations                        │
│  • /api/sessions - Session management                     │
│  • /ws - Real-time communication                          │
│  • /health - Server status                                │
└──────────────┬───────────────────────────────────────────┘
               │ Python async calls
               ↓
┌──────────────────────────────────────────────────────────┐
│         In-Process Agent (LangGraph-based)                │
│  • Message processing                                     │
│  • LLM interaction                                        │
│  • Context management (via LangGraph middleware)          │
│  • Tool execution (future)                                │
└──────────────┬───────────────────────────────────────────┘
               │ HTTP/gRPC to LLM API
               ↓
┌──────────────────────────────────────────────────────────┐
│           External LLM Providers                          │
│  • OpenAI (GPT-4, GPT-3.5)                                │
│  • AWS Bedrock (Claude, etc)                              │
│  • OpenAI-compatible (Ollama, vLLM, etc)                  │
└──────────────────────────────────────────────────────────┘
```

---

## Component Details

### 1. TUI Client (`client/tui/`)

**Purpose**: Interactive terminal interface for users

**Key Components**:
- `app.py` - Main Textual application
- `screens/` - Screen definitions (project picker, session chat)
- `widgets/` - Reusable UI components (messages, input, status bar)
- `api.py` - REST client (httpx-based)
- `websocket.py` - WebSocket handler (asyncio)

**Responsibilities**:
- Display projects and sessions
- Handle user input
- Stream WebSocket events to UI
- Render assistant responses and tool outputs

### 2. FastAPI Server (`server/app/`)

**Purpose**: Central API and orchestration layer

**Key Components**:
- `main.py` - FastAPI app with routes and WebSocket endpoint
- `settings.py` - Configuration from environment
- `protocol/` - Message and event schemas
- `observability/` - Logging and OTEL tracing

**Routes**:
- `GET /health` - Server health check
- `GET /api/projects` - List projects
- `POST /api/projects` - Create project
- `WebSocket /ws` - Real-time communication

### 3. In-Process Agent (`server/app/agent/`)

**Purpose**: AI reasoning engine and LLM orchestration

**Key Components**:
- `runtime.py` - `InProcessAgent` class (LangGraph state machine)
- `llm_handler.py` - LLM provider routing and API calls
- `manager.py` - `AgentManager` (agent lifecycle)
- `policies.py` - System prompts and agent configuration

**Architecture**:
```python
InProcessAgent (LangGraph-based):
  ├── Build state graph with nodes
  ├── "process_input" node
  ├── "call_llm" node
  └── Compile to runnable
```

### 4. Session Manager (`server/app/sessions/`)

**Purpose**: Session lifecycle and state management

**Key Components**:
- `manager.py` - `SessionManager` (create, resume, disconnect sessions)
- `workspace.py` - Workspace path management
- Stores sessions in memory (SQLite backend ready)

**Data Model**:
```python
@dataclass
class Session:
    session_id: str
    project_id: str
    network_mode: str
    workspace_path: str
    websocket: WebSocket | None
    history: list[dict]
```

### 5. Project Manager (`server/app/projects/`)

**Purpose**: Project metadata and persistence

**Key Components**:
- `manager.py` - `ProjectManager` (CRUD operations)
- `project.py` - Project data model
- `persistence.py` - File-based storage
- `cleanup.py` - Auto-cleanup task

**Data Model**:
```python
@dataclass
class Project:
    project_id: str
    user_prefix: str
    config: ProjectConfig
    sessions: list[SessionRecord]
    created_at: datetime
    last_accessed: datetime
```

---

## Data Flow

### Creating a Project

```
1. User Input (TUI)
   └─→ api.create_project(user_prefix, network_mode, repo_url)

2. REST Call
   └─→ POST /api/projects

3. Server Processing
   ├─→ ProjectManager.create_project()
   ├─→ WorkspaceManager.create_workspace()
   ├─→ Git.clone_repo() [if repo_url provided]
   └─→ Return project_id + workspace_path

4. TUI Display
   └─→ Add to project list
```

### Starting a Session

```
1. User Action (TUI)
   └─→ Select project → "Start session"

2. WebSocket Connect
   └─→ ws://localhost:8000/ws

3. Send Message
   └─→ CreateSessionRequest(project_id, user_prefix, network_mode)

4. Server Processing
   ├─→ SessionManager.create_or_resume_session()
   ├─→ AgentManager.create_agent(session_id)
   │   ├─→ InProcessAgent.__init__()
   │   ├─→ LLMHandler.__init__()
   │   └─→ LangGraph state machine compilation
   ├─→ WorkspaceManager.setup_workspace()
   └─→ Send SessionStartedEvent

5. TUI Ready
   └─→ Display chat interface
   └─→ Ready for user input
```

### Processing User Message

```
1. User Types + Enter
   └─→ WebSocket: UserMessage(session_id, content)

2. Server Handler
   ├─→ Validate session exists
   ├─→ SessionManager.add_to_history("user", content)
   ├─→ Get agent: AgentManager.get_agent(session_id)
   └─→ Send to agent: agent.process_message(content)

3. Agent Processing
   ├─→ LangGraph state machine invokes nodes
   ├─→ Prepare context (conversation history, files, etc)
   ├─→ Call LLM: LLMHandler.generate_response(messages)
   └─→ Return response text

4. LLM API Call
   ├─→ Determine provider (OpenAI vs Bedrock vs other)
   ├─→ Format request according to provider API
   ├─→ Send HTTP request
   └─→ Parse and return response

5. Server Response
   ├─→ Add response to history: add_to_history("assistant", response)
   ├─→ WebSocket: AssistantMessageEvent(response)
   └─→ Await next user input

6. TUI Display
   ├─→ Receive event
   ├─→ Format response
   ├─→ Display in chat
   └─→ Ready for next message
```

---

## LLM Provider Routing

**LLMHandler** abstracts multiple providers:

```python
class LLMHandler:
    __init__(settings):
        if settings.llm_provider == "openai":
            self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        elif settings.llm_provider == "bedrock":
            self.client = boto3.client("bedrock-runtime")
        elif settings.llm_provider == "openai_compatible":
            self.client = AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_api_base
            )
    
    async def generate_response(messages, **kwargs):
        if self.provider in ("openai", "openai_compatible"):
            return await self._call_openai(messages, **kwargs)
        elif self.provider == "bedrock":
            return await self._call_bedrock(messages, **kwargs)
```

**Configuration**:
```bash
# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...

# AWS Bedrock
LLM_PROVIDER=bedrock
AWS_PROFILE=default

# Local Ollama
LLM_PROVIDER=openai_compatible
OPENAI_API_BASE=http://localhost:11434/v1
OPENAI_API_KEY=any-key
```

---

## State Management

### InProcessAgent State (LangGraph)

```python
class MessagesState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
```

### Session History

```python
[
    {
        "role": "user",
        "content": "Explain this code...",
        "timestamp": "2024-02-11T12:34:56Z"
    },
    {
        "role": "assistant",
        "content": "This code does...",
        "timestamp": "2024-02-11T12:34:57Z",
        "metadata": {
            "model": "gpt-4",
            "tokens_used": 150
        }
    }
]
```

---

## Error Handling

All errors wrapped in custom exception hierarchy:

```python
CognitionError (base)
├── SessionError
│   ├── SessionNotFoundError
│   └── SessionLimitError
├── ProjectError
│   └── ProjectNotFoundError
└── AgentError
    └── LLMError
```

---

## Observability

### Logging (structlog)

```python
logger.info(
    "Session created",
    session_id="...",
    project_id="...",
    network_mode="OFF",
    duration_ms=123
)
```

### Tracing (OpenTelemetry)

- LLM API calls (latency, model, tokens)
- Session operations (create, resume, disconnect)
- Message processing (duration, tool calls)
- Exports to Jaeger/Tempo/custom OTLP backend

### Metrics (Future)

- LLM call latency histogram
- Session creation time distribution
- Message processing throughput
- Error rates by type

---

## Testing

**156 Unit Tests Passing**:
- Client tests (API, WebSocket, widgets, screens)
- Server tests (routes, backends, protocols)
- Agent tests (runtime, LLM router)
- Session tests (manager, workspace)

**Test Execution**: ~0.6 seconds

---

## Deployment Models

### Model 1: Desktop (Current)

```
Single Machine:
├── FastAPI Server (localhost:8000)
├── TUI Client
├── In-process agents
└── Workspace directory
```

**Usage**: `uv run python -m tui.app`

### Model 2: Server + Remote Clients (Future)

```
Server Machine              Client Machine(s)
├── FastAPI Server         └── TUI/Web UI
├── Agent pool             └── Connect to server
└── Shared workspace       └── SSH or HTTP tunnel
```

### Model 3: PaaS (Planned - 6 months)

```
Load Balancer (public API)
├── API Gateway
├── Agent Spawner Service (K8s)
├── Agent Pod Pool
├── Multi-tenant RBAC
└── Audit logging
```

---

## Known Limitations

| Limitation | Impact | Timeline |
|-----------|--------|----------|
| No file tools | Can't read/edit project files | Phase 4 |
| No bash execution | Can't run tests/commands | Phase 4 |
| Ephemeral sessions | History lost on disconnect | Phase 2 |
| Fixed context window | Can't auto-compress history | Phase 3 |
| No streaming | Wait for full LLM response | Phase 3 |

---

## Future Enhancements

### Phase 3 (Next 2 weeks)
- [ ] OTEL observability polish
- [ ] Code analysis tools (file read, search)
- [ ] Project templates

### Phase 4 (6-8 weeks)
- [ ] Tool system (bash, file editing)
- [ ] Session persistence (SQLite)
- [ ] LLM response streaming
- [ ] Advanced context management

### Phase 5 (10-12 weeks)
- [ ] Web UI (React)
- [ ] Multi-region deployment
- [ ] Agent marketplace
- [ ] Custom agent builder

---

## References

- **LangGraph**: https://python.langchain.com/docs/langgraph/
- **FastAPI**: https://fastapi.tiangolo.com/
- **Textual**: https://textual.textualize.io/
- **OpenTelemetry**: https://opentelemetry.io/
- **Pydantic**: https://docs.pydantic.dev/
