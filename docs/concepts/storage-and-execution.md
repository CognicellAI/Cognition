# Storage & Execution

Cognition decouples *where state is stored* from *where code runs* through two independent protocol abstractions: `StorageBackend` (Layer 2) and the execution backends (Layer 3). Both are pluggable — swap implementations via configuration with no code changes in any layer above.

---

## StorageBackend Protocol

Defined in `server/app/storage/backend.py`. The protocol is composed of three sub-protocols, each responsible for a distinct concern:

### SessionStore

```python
class SessionStore(Protocol):
    async def create_session(
        self,
        session_id: str,
        workspace_path: str,
        title: str,
        thread_id: str,
        scopes: dict[str, str],
        agent_name: str,
    ) -> Session: ...

    async def get_session(self, session_id: str) -> Session | None: ...

    async def list_sessions(
        self,
        workspace_path: str,
        filter_scopes: dict[str, str] | None = None,
    ) -> list[Session]: ...

    async def update_session(
        self,
        session_id: str,
        title: str | None = None,
        status: SessionStatus | None = None,
        agent_name: str | None = None,
    ) -> Session | None: ...

    async def update_message_count(self, session_id: str, delta: int) -> None: ...

    async def delete_session(self, session_id: str) -> bool: ...
```

### MessageStore

```python
class MessageStore(Protocol):
    async def create_message(self, message: Message) -> Message: ...

    async def get_message(self, message_id: str) -> Message | None: ...

    async def get_messages_by_session(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Message], int]: ...  # (messages, total_count)

    async def list_messages_for_session(self, session_id: str) -> list[Message]: ...

    async def delete_messages_for_session(self, session_id: str) -> int: ...
```

### CheckpointerStore

```python
class CheckpointerStore(Protocol):
    async def get_checkpointer(self) -> BaseCheckpointSaver: ...
    async def close_checkpointer(self) -> None: ...
```

The checkpointer is passed to LangGraph and stores agent state at every step — enabling resumable workflows that survive server restarts.

### Unified StorageBackend

`StorageBackend` combines all three plus lifecycle methods:

```python
class StorageBackend(SessionStore, MessageStore, CheckpointerStore, Protocol):
    async def initialize(self) -> None: ...   # Create tables, pools, migrations
    async def close(self) -> None: ...        # Drain connections, release resources
    async def health_check(self) -> bool: ...
```

---

## Storage Implementations

`server/app/storage/factory.py` — `create_storage_backend(settings)` creates the backend:

```python
match settings.persistence_backend:
    case "sqlite":   return SqliteStorageBackend(settings)
    case "postgres": return PostgresStorageBackend(settings)
    case "memory":   return MemoryStorageBackend(settings)
    case _:          raise StorageBackendError(f"Unknown backend: ...")
```

**No silent fallback.** An unrecognised `COGNITION_PERSISTENCE_BACKEND` value raises immediately at startup.

### SQLite (`server/app/storage/sqlite.py`)

Default development backend.

- Async I/O via `aiosqlite`
- LangGraph checkpoints via `AsyncSqliteSaver`
- Database path resolved relative to workspace if not absolute
- Parent directories created automatically
- Suitable for single-node deployments; not safe for concurrent multi-process access

**Configuration:**
```env
COGNITION_PERSISTENCE_BACKEND=sqlite
COGNITION_PERSISTENCE_URI=.cognition/state.db
```

### PostgreSQL (`server/app/storage/postgres.py`)

Production backend for multi-node or high-availability deployments.

- Async I/O via `asyncpg` connection pool (default: 1–10 connections)
- LangGraph checkpoints via `AsyncPostgresSaver`
- Schema managed by Alembic migrations
- DSN normalisation: `postgresql+asyncpg://` → `postgresql://` for asyncpg compatibility

**Configuration:**
```env
COGNITION_PERSISTENCE_BACKEND=postgres
COGNITION_PERSISTENCE_URI=postgresql://user:pass@host:5432/cognition
```

### Memory (`server/app/storage/memory.py`)

In-process dict-backed store used in unit tests.

- Zero dependencies
- State lost on process exit
- Fastest possible; no I/O overhead

**Configuration:**
```env
COGNITION_PERSISTENCE_BACKEND=memory
```

---

## ExecutionBackend

Code execution is isolated from the server process. Cognition uses two backend types, both ultimately relying on `DockerExecutionBackend` for hard isolation.

### DockerExecutionBackend (`server/app/execution/backend.py`)

Runs commands in a Docker container with full kernel-level isolation:

| Security Control | Value |
|---|---|
| Linux capabilities | All dropped (`cap_drop: ALL`) |
| Privilege escalation | Blocked (`no-new-privileges: true`) |
| Root filesystem | Read-only (`read_only: true`) |
| Writable paths | `/tmp` and `/home` via `tmpfs` mounts |
| Network | Configurable; `none` by default |
| Memory limit | Configurable (default: `512m`) |
| CPU limit | Configurable (default: `1.0` core) |

Container lifecycle: the backend checks for an existing running container for the session before creating a new one. Containers are reused within a session for performance. Command output is truncated at 100 KB.

### Sandbox Backends (`server/app/agent/sandbox_backend.py`)

The two sandbox backends are Cognition's concrete wrappers around the execution abstraction:

#### CognitionLocalSandboxBackend

Commands execute in the local process under the server's user.

- Command parsing with `shlex.split()` + `subprocess` with `shell=False` — no shell injection possible
- Protected paths list (`.cognition/` by default): write operations that target protected paths are blocked before execution
- File operations operate directly on the host filesystem
- No process isolation from the Cognition server process

Best for: local development, trusted codebases, CI pipelines.

```env
COGNITION_SANDBOX_BACKEND=local
```

#### CognitionDockerSandboxBackend

File operations run directly on the host filesystem (for performance); command execution is routed through `DockerExecutionBackend` (for isolation).

- Each session gets its own container (lazy creation on first command)
- Container is reused for the session lifetime
- Requires Docker daemon and `cognition-sandbox:latest` image
- `host_workspace` setting maps the workspace path into the container

Best for: production, multi-tenant deployments, any untrusted code.

```env
COGNITION_SANDBOX_BACKEND=docker
COGNITION_DOCKER_IMAGE=cognition-sandbox:latest
COGNITION_DOCKER_NETWORK=none
COGNITION_DOCKER_MEMORY_LIMIT=512m
COGNITION_DOCKER_CPU_LIMIT=1.0
COGNITION_DOCKER_TIMEOUT=300
```

### Factory

```python
from server.app.agent.sandbox_backend import create_sandbox_backend

backend = create_sandbox_backend(settings)
# Returns CognitionLocalSandboxBackend or CognitionDockerSandboxBackend
```

---

## How Storage and Execution Compose

A session involves both layers simultaneously:

```
Client sends message
        │
        ▼
Layer 6: API persists user message in StorageBackend
        │
        ▼
Layer 4: AgentRuntime streams events
        │
        ├── Tool call: bash("ls -la")
        │       └── Layer 3: SandboxBackend.execute("ls -la")
        │               └── Returns ExecutionResult(output, exit_code)
        │
        └── Stream complete (done event)
                └── Layer 6: API persists assistant message in StorageBackend
```

The storage and execution backends never call each other. Composition happens only at Layer 4 and Layer 6 — the correct level in the dependency hierarchy.

---

## Built-in Tools

Beyond the sandbox backends, the agent has three built-in tools provided by `server/app/agent/tools.py`:

| Tool | Class | Description |
|---|---|---|
| `browser` | `BrowserTool` | Fetch web pages as text, markdown, or HTML via `httpx` |
| `search` | `SearchTool` | DuckDuckGo web search, returns titles, links, and snippets |
| `inspect_package` | `InspectPackageTool` | Inspect Python packages: list submodules, classes, and functions |

These run in the local process (not inside the Docker sandbox) and are always available regardless of `sandbox_backend` setting.

---

## Circuit Breaker (`server/app/execution/circuit_breaker.py`)

The circuit breaker protects downstream LLM providers from cascading failures. The same implementation is used by the LLM provider fallback chain.

States:

```
CLOSED ──[failures ≥ threshold]──► OPEN
  ▲                                   │
  │                          [timeout expires]
  │                                   ▼
  └──[successes ≥ threshold]── HALF_OPEN
```

Default configuration:
- `failure_threshold`: 5 consecutive failures to open
- `success_threshold`: 3 consecutive successes to close from half-open
- `timeout_seconds`: 60 s in open state before transitioning to half-open
- `half_open_max_calls`: 3 test calls allowed in half-open state

Circuit breaker status is reported in `/health`:

```json
{
  "circuit_breakers": {
    "openai": {
      "state": "closed",
      "total_calls": 142,
      "failed_calls": 1,
      "consecutive_failures": 0
    }
  }
}
```
