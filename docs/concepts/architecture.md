# Architecture

Cognition is a **headless agent orchestration backend** built on a strict 7-layer architecture. Each layer has a single responsibility, dependencies only flow downward, and no layer imports from a layer above it.

The core promise: define your agent with tools, skills, and a system prompt — Cognition provides the REST API, SSE streaming, durable persistence, sandboxed execution, multi-tenant isolation, and full observability automatically.

---

## Contents

- [The 7-Layer Model](#the-7-layer-model)
- [Layer Breakdown](#layer-breakdown)
  - [Layer 1 — Foundation](#layer-1--foundation)
  - [Layer 2 — Persistence](#layer-2--persistence)
  - [Layer 3 — Execution](#layer-3--execution)
  - [Layer 4 — Agent Runtime](#layer-4--agent-runtime)
  - [Layer 5 — LLM Provider](#layer-5--llm-provider)
  - [Layer 6 — API & Streaming](#layer-6--api--streaming)
  - [Layer 7 — Observability](#layer-7--observability)
- [Startup Sequence](#startup-sequence)
- [The North Star](#the-north-star)

---

## The 7-Layer Model

```
┌─────────────────────────────────────────────────────────┐
│  Layer 7: OBSERVABILITY                                   │
│  OTel traces · Prometheus metrics · MLflow experiments    │
│  server/app/observability/                                │
├─────────────────────────────────────────────────────────┤
│  Layer 6: API & STREAMING                                 │
│  FastAPI routes · SSE streams · Session scoping           │
│  server/app/api/                                          │
├─────────────────────────────────────────────────────────┤
│  Layer 5: LLM PROVIDER                                    │
│  ConfigRegistry · ModelCatalog · init_chat_model          │
│  server/app/llm/                                          │
├─────────────────────────────────────────────────────────┤
│  Layer 4: AGENT RUNTIME                                   │
│  AgentRuntime protocol · AgentDefinition · Agent registry │
│  server/app/agent/                                        │
├─────────────────────────────────────────────────────────┤
│  Layer 3: EXECUTION                                       │
│  Sandbox protocol · Local · Docker                        │
│  server/app/execution/  server/app/agent/sandbox_backend  │
├─────────────────────────────────────────────────────────┤
│  Layer 2: PERSISTENCE                                     │
│  StorageBackend · ConfigRegistry · SQLite · PostgreSQL    │
│  server/app/storage/                                      │
├─────────────────────────────────────────────────────────┤
│  Layer 1: FOUNDATION                                      │
│  Settings · Exceptions · ConfigLoader · Bootstrap         │
│  server/app/settings.py · exceptions.py · bootstrap.py   │
└─────────────────────────────────────────────────────────┘
```

The dependency rule is absolute: a layer may only import from layers below it. Layer 6 (API) calls Layer 4 (Agent Runtime), which calls Layer 3 (Execution) and Layer 2 (Persistence). No upward imports.

---

## Layer Breakdown

### Layer 1 — Foundation

The bedrock of the system. Everything else builds on these components.

**`server/app/settings.py`** — A single `Settings` class (Pydantic v2 `BaseSettings`) holding all infrastructure configuration. Loaded via a 4-level hierarchy: built-in defaults → `~/.cognition/config.yaml` → `.cognition/config.yaml` → environment variables. The highest-precedence source wins.

Infrastructure settings (server, persistence, sandbox, CORS, scoping, observability, rate limiting) live here permanently. Agent and provider configuration has moved to the `ConfigRegistry` in Layer 2 — these are hot-reloadable and API-manageable. See the [Configuration guide](../guides/configuration.md) for all fields.

**`server/app/exceptions.py`** — A typed exception hierarchy rooted at `CognitionError`. Every subsystem raises a domain-specific subclass (`SessionNotFoundError`, `LLMUnavailableError`, `ToolExecutionError`, etc.) rather than bare exceptions. All exceptions carry an `ErrorCode` enum value and an optional `details` dict for structured error reporting.

**`server/app/config_loader.py`** — Merges YAML config files and resolves them into settings. Searches for `.cognition/config.yaml` by walking up from the current working directory, enabling project-local configuration without explicit paths.

---

### Layer 2 — Persistence

All durable state lives in this layer behind two protocol abstractions: `StorageBackend` for session/message/checkpoint data, and `ConfigRegistry` for hot-reloadable agent/provider/skill/tool configuration.

#### StorageBackend

**`server/app/storage/backend.py`** — Four composable `Protocol` classes:

- `SessionStore` — CRUD for sessions (create, get, list with scope filtering, update, delete)
- `MessageStore` — CRUD for messages (create, get, list with pagination, delete by session)
- `CheckpointerStore` — LangGraph checkpoint persistence (`get_checkpointer`, `close_checkpointer`)
- `get_store()` → `BaseStore | None` — LangGraph cross-thread memory store for persistent agent memories

The unified `StorageBackend` protocol combines all four plus lifecycle methods (`initialize`, `close`, `health_check`). Swapping backends requires no changes to any code above Layer 2.

**Implementations** (`server/app/storage/`):

| Backend | Module | Checkpointer | Store |
|---|---|---|---|
| `SqliteStorageBackend` | `sqlite.py` | `AsyncSqliteSaver` | `AsyncSqliteStore` |
| `PostgresStorageBackend` | `postgres.py` | `AsyncPostgresSaver` | `AsyncPostgresStore` |
| `MemoryStorageBackend` | `memory.py` | `InMemorySaver` | `InMemoryStore` |

**`server/app/storage/factory.py`** — `create_storage_backend(settings)` reads `settings.persistence_backend` and returns the correct implementation. Unknown backend values raise `StorageBackendError` — no silent fallback.

#### ConfigRegistry

**`server/app/storage/config_registry.py`** — The `ConfigRegistry` is a scoped, hot-reloadable key-value store for agent/provider/skill/tool configuration. It replaces environment variables and YAML for these concerns — all of which can now be changed at runtime via the REST API without a server restart.

**Implementations**:

| Implementation | Backed by | Hot-reload mechanism |
|---|---|---|
| `SqliteConfigRegistry` | SQLite (`config_entities` table) | `InProcessDispatcher` (in-memory pub/sub) |
| `PostgresConfigRegistry` | Postgres (`config_entities` table) | `PostgresListenDispatcher` (LISTEN/NOTIFY) |
| `MemoryConfigRegistry` | In-memory dict | `InProcessDispatcher` |

Each entry has `(entity_type, name, scope, definition)`. The `scope` column restricts which requests see the entry — entries with empty scope `{}` are global. Scope resolution walks from most-specific to global.

**`server/app/storage/config_dispatcher.py`** — `ConfigChangeDispatcher` invalidates in-process caches on every write:
- `InProcessDispatcher` — zero-latency, same-process pub/sub (SQLite, single-node)
- `PostgresListenDispatcher` — maintains a persistent `LISTEN cognition_config_changes` connection; near-real-time invalidation across multiple server instances (no external broker required)

---

### Layer 3 — Execution

Code execution is isolated from the server process using pluggable backends.

**`server/app/execution/backend.py`** — `DockerExecutionBackend` runs commands in a Docker container with:
- Kernel-level namespace isolation
- All Linux capabilities dropped (`cap_drop=ALL`)
- `no-new-privileges` security option
- Read-only root filesystem
- `tmpfs` mounts for `/tmp` and `/home`
- Configurable memory and CPU limits
- Network isolation (`network_mode=none` by default)

**`server/app/agent/sandbox_backend.py`** — Two Cognition-specific backends:

- `CognitionLocalSandboxBackend` — Commands executed in the local process using `shlex.split()` + `shell=False`. Protected paths (`.cognition/` by default) block write operations. Per-command `timeout` override supported. No `shell=True` anywhere.
- `CognitionDockerSandboxBackend` — File operations run directly on the host filesystem; command execution is routed through `DockerExecutionBackend`. Container is created lazily and reused within a session.

`create_sandbox_backend(settings)` selects between them based on `settings.sandbox_backend`.

---

### Layer 4 — Agent Runtime

The agent runtime is the brain of the system. It translates high-level `AgentDefinition` objects into running agents, normalizes all Deep Agents events into a canonical stream, and manages the agent lifecycle.

#### AgentRuntime Protocol

**`server/app/agent/runtime.py`** — The `AgentRuntime` protocol:

```python
class AgentRuntime(Protocol):
    async def astream_events(
        self,
        input_data: str | dict[str, Any],
        thread_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]: ...

    async def ainvoke(
        self,
        input_data: str | dict[str, Any],
        thread_id: str | None = None,
    ) -> AgentEvent: ...

    async def get_state(
        self, thread_id: str | None = None
    ) -> dict[str, Any] | None: ...

    async def abort(self, thread_id: str | None = None) -> bool: ...

    async def get_checkpointer(self) -> BaseCheckpointSaver: ...
```

`DeepAgentRuntime` is the concrete implementation. It wraps Deep Agents and uses `astream(stream_mode=["messages", "updates", "custom"], subgraphs=True, version="v2")` to transform events into the canonical `AgentEvent` types. Abort is handled via a thread-ID-based cancellation set. An optional `context` parameter (`CognitionContext`) is forwarded to `astream()` for per-user Store namespace scoping.

#### Canonical Event Types

| Event | Key Fields | Description |
|---|---|---|
| `TokenEvent` | `content: str` | A single streaming LLM token |
| `ToolCallEvent` | `name`, `args`, `id` | Agent invoking a tool; `id` correlates with `ToolResultEvent.tool_call_id` |
| `ToolResultEvent` | `tool_call_id`, `output`, `exit_code` | Tool execution result |
| `PlanningEvent` | `todos: list[str]` | Agent creating a task plan |
| `StepCompleteEvent` | `step_number`, `total_steps`, `description` | Plan step finished |
| `DelegationEvent` | `from_agent`, `to_agent`, `task` | Primary agent delegating to subagent |
| `StatusEvent` | `status: str` | `"thinking"` or `"idle"` |
| `UsageEvent` | `input_tokens`, `output_tokens`, `estimated_cost`, `provider`, `model` | Token accounting |
| `DoneEvent` | `assistant_data` | Stream complete |
| `ErrorEvent` | `message`, `code` | Fatal error; stream terminates |

#### AgentDefinition

**`server/app/agent/definition.py`** — `AgentDefinition` is a Pydantic model that fully describes an agent:

```python
class AgentDefinition(BaseModel):
    name: str
    system_prompt: str | PromptConfig | None
    tools: list[str]                  # dotted import paths or ConfigRegistry tool names
    skills: list[str]                 # paths to SKILL.md files or directories
    memory: list[str]                 # paths to AGENTS.md-style instruction files
    subagents: list[SubagentDefinition]
    interrupt_on: dict[str, bool]     # tool_name -> require approval before execution
    middleware: list[str | dict]      # declarative middleware names or {name, **kwargs}
    config: AgentConfig               # per-agent provider/model/temperature overrides
    mode: Literal["primary", "subagent", "all"]
    description: str | None
    hidden: bool
    native: bool                      # True for built-in agents (default, readonly)
```

`AgentConfig` carries per-agent LLM overrides that slot between the global ConfigRegistry default and any session-level override:

```python
class AgentConfig(BaseModel):
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    recursion_limit: int | None = None
```

Definitions can be loaded from YAML files (`load_agent_definition`), Markdown with YAML frontmatter (`load_agent_definition_from_markdown`), or created via `POST /agents` (stored in ConfigRegistry).

#### Agent Registry

**`server/app/agent/agent_definition_registry.py`** — `AgentDefinitionRegistry` is the in-memory catalog of available agents. It is seeded from two sources on startup and kept in sync by the `ConfigChangeDispatcher`:

1. **Built-in agents** — always reseeded from code: `default` (full-access, `primary`) and `readonly` (analysis-only, `primary`)
2. **File-based agents** — from `.cognition/agents/*.md` and `.cognition/agents/*.yaml`; hot-reloaded by the file watcher
3. **API agents** — from ConfigRegistry (`POST /agents`); invalidated via `ConfigChangeDispatcher`

Key methods: `get_all()`, `get(name)`, `primaries()`, `subagents()`, `reload()`, `is_valid_primary(name)`.

#### Agent Factory

**`server/app/agent/cognition_agent.py`** — `create_cognition_agent()` is the async factory that instantiates a Deep Agent from an `AgentDefinition`. In order:

1. Selects the sandbox backend (`local` or `docker`)
2. Loads built-in tools: `BrowserTool`, `SearchTool`, `InspectPackageTool`
3. Loads MCP tools from configured remote servers
4. Resolves tools from `AgentDefinition.tools` (dotted import paths)
5. Loads API-registered tools from `ConfigRegistry.list_tools(scope)`
6. Attaches the middleware stack: `ToolSecurityMiddleware` (COGNITION_BLOCKED_TOOLS deny-list), `CognitionObservabilityMiddleware` (Prometheus), `CognitionStreamingMiddleware` (status events)
7. Resolves declarative upstream middleware from the definition
8. Injects subagents as Deep Agents `SubAgent` dicts
9. Passes `store=` (LangGraph `BaseStore` from `storage_backend.get_store()`) and `context_schema=CognitionContext`

Agent instances are cached by an MD5 hash of their definition. The cache is invalidated by `ConfigChangeDispatcher` on any agent/tool/skill change.

#### CognitionContext

`CognitionContext` is a typed invocation context built from `session.scopes` and forwarded to every `astream()` / `ainvoke()` call:

```python
@dataclass
class CognitionContext:
    user_id: str = "anonymous"
    org_id: str | None = None
    project_id: str | None = None
    extra: dict[str, str] = field(default_factory=dict)
```

Nodes and middleware access it via `runtime.context`. It provides the primary key for scoping LangGraph Store namespaces — ensuring user A cannot read user B's cross-session memories.

---

### Layer 5 — LLM Provider

**`server/app/llm/model_catalog.py`** — `ModelCatalog` fetches and caches the models.dev catalog (configurable URL, default 1-hour TTL). Provides enriched model metadata — context windows, tool call support, pricing, modalities — for API responses and validation warnings. The catalog is enrichment only: if unreachable, endpoints degrade gracefully.

**`server/app/llm/deep_agent_service.py`** — `DeepAgentStreamingService` is the per-session streaming coordinator. It resolves the LLM provider from ConfigRegistry and drives the agent via `DeepAgentRuntime`. Provider resolution follows a strict priority chain — the first match wins, no fallback:

1. `SessionConfig.provider_id` — exact `ProviderConfig` lookup by ID from ConfigRegistry
2. `SessionConfig.provider` + `SessionConfig.model` — direct session override
3. `AgentDefinition.config.provider` + `.model` — per-agent definition override
4. First enabled `ProviderConfig` from ConfigRegistry (sorted by `priority` ascending)

If no provider is resolved, `LLMProviderConfigError` is raised immediately with an actionable message. `_build_model()` uses LangChain's `init_chat_model()` to construct the model instance.

`SessionAgentManager` is the server-level singleton that manages one `DeepAgentStreamingService` per active session and routes abort signals to the correct session.

---

### Layer 6 — API & Streaming

**`server/app/api/routes/`** — FastAPI route handlers for all resources: sessions, messages, agents, skills, tools, models, config. Routes do not contain business logic; they validate inputs, call into Layer 4 or Layer 2, and serialize outputs.

**Route inventory:**

| Prefix | CRUD | Description |
|---|---|---|
| `/sessions` | `POST GET PATCH DELETE` | Session lifecycle |
| `/sessions/{id}/messages` | `POST GET` | Message send (streaming) and history |
| `/sessions/{id}/abort` | `POST` | Cancel in-progress execution |
| `/agents` | `GET POST PUT PATCH DELETE` | Agent definitions (ConfigRegistry) |
| `/skills` | `GET POST PUT PATCH DELETE` | Skill definitions (ConfigRegistry) |
| `/tools` | `GET POST DELETE` + `/reload` `/errors` | Tool registry |
| `/models` | `GET` | Model catalog |
| `/models/providers` | `GET POST PATCH DELETE` + `/test` | Provider configs (ConfigRegistry) |
| `/config` | `GET PATCH` + `/rollback` | Infrastructure config |
| `/health` `/ready` | `GET` | Health and readiness probes |

**`server/app/api/sse.py`** — `SSEStream` implements the SSE protocol with:
- Automatic reconnection via `Last-Event-ID` header and `EventBuffer` replay
- Heartbeat comments (`:heartbeat`) every 15 seconds to keep proxies alive
- Sequential event IDs for ordering and gap detection
- `EventBuilder` static factory for every event type

**`server/app/api/scoping.py`** — `create_scope_dependency()` builds a FastAPI dependency that reads `x-cognition-scope-{key}` headers for each key in `settings.scope_keys`. When `scoping_enabled=true`, missing headers return `403 Forbidden` (fail-closed). Scope values filter sessions and ConfigRegistry entries to enforce tenant isolation.

**`server/app/api/middleware.py`** — `SecurityHeadersMiddleware` adds `X-Content-Type-Options`, `X-Frame-Options`, and `X-XSS-Protection` to every response. `ObservabilityMiddleware` records request count and duration into Prometheus.

---

### Layer 7 — Observability

**`server/app/observability/__init__.py`** — Three independent subsystems, all with graceful degradation:

- `setup_tracing()` — OpenTelemetry with OTLP exporter, FastAPI auto-instrumentation, LangChain auto-instrumentation. Uses gRPC or HTTP transport depending on the endpoint URL.
- `setup_metrics()` — Prometheus metrics server on a separate port. Defines `REQUEST_COUNT`, `REQUEST_DURATION`, `LLM_CALL_DURATION`, `TOOL_CALL_COUNT`, `SESSION_COUNT` counters and histograms. Falls back to `DummyMetric` when `prometheus_client` is not installed.
- `setup_logging()` — structlog with JSON rendering in production and console rendering in development.

**`server/app/observability/mlflow_config.py`** — `setup_mlflow_tracing(settings)` sets the tracking URI and creates or sets the experiment. MLflow receives traces via the OTel Collector — there is no direct MLflow SDK call in the hot path.

---

## Startup Sequence

`server/app/main.py` wires all layers together in its lifespan context manager, in strict dependency order:

```
 1. Layer 2: Initialize storage backend (SQLite / Postgres / Memory)
 2. Layer 2: Initialize ConfigRegistry (same backend, config_entities table)
 3. Layer 1: Bootstrap providers from config.yaml llm: section (seed_if_absent)
 4. Layer 4: Initialize agent definition registry (built-ins seeded)
 5. Layer 4: Seed agent definitions from ConfigRegistry (API-created agents)
 6. Layer 2: Start ConfigChangeDispatcher (InProcess or Postgres LISTEN)
 7. Layer 4: Initialize session manager
 8. Layer 4: Initialize agent registry (tool auto-discovery from .cognition/tools/)
 9. Layer 4: Start file watcher for .cognition/ hot-reload
10. Layer 7: Setup OTel tracing
11. Layer 7: Setup Prometheus metrics
12. Layer 7: Setup MLflow
13. Layer 6: Start rate limiter
```

Shutdown reverses: stop file watcher → stop rate limiter → stop ConfigChangeDispatcher → close storage backend.

---

## The North Star

The architectural goal is a single declarative entry point:

```python
from cognition import AgentDefinition, Cognition

agent = AgentDefinition(
    tools=[my_tool, another_tool],
    skills=["deploy-app"],
    system_prompt="You are a deployment expert.",
)

app = Cognition(agent)
app.run()
```

This one call should provision the full 7-layer stack: REST API, SSE streaming, SQLite/Postgres persistence, local/Docker sandbox, LangGraph Store for cross-session memory, OTel tracing, Prometheus metrics, multi-tenant scoping, rate limiting, and an evaluation pipeline. All layers, all infrastructure, from a single agent definition.
