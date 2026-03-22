# Architecture

Cognition is a **headless agent orchestration backend** built on a strict 7-layer architecture. Each layer has a single responsibility, dependencies only flow downward, and no layer imports from a layer above it.

The core promise: define your agent with tools, skills, and a system prompt — Cognition provides the REST API, SSE streaming, durable persistence, sandboxed execution, multi-tenant isolation, and full observability automatically.

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
│  StorageBackend protocol · SQLite · PostgreSQL · Memory   │
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

The bedrock of the system. Everything else builds on these three components.

**`server/app/settings.py`** — A single `Settings` class (Pydantic v2 `BaseSettings`) holding all configuration. Loaded via a 4-level hierarchy: built-in defaults → `~/.cognition/config.yaml` → `.cognition/config.yaml` → environment variables. The highest-precedence source wins. See the [Configuration guide](../guides/configuration.md) for all fields.

**`server/app/exceptions.py`** — A typed exception hierarchy rooted at `CognitionError`. Every subsystem raises a domain-specific subclass (`SessionNotFoundError`, `LLMUnavailableError`, `ToolExecutionError`, etc.) rather than bare exceptions. All exceptions carry an `ErrorCode` enum value and an optional `details` dict for structured error reporting.

**`server/app/config_loader.py`** — Merges YAML config files and resolves them into settings. Searches for `.cognition/config.yaml` by walking up from the current working directory, enabling project-local configuration without explicit paths.

---

### Layer 2 — Persistence

All durable state lives in this layer behind the `StorageBackend` protocol.

**`server/app/storage/backend.py`** — Three composable `Protocol` classes:

- `SessionStore` — CRUD for sessions (create, get, list with scope filtering, update, delete)
- `MessageStore` — CRUD for messages (create, get, list with pagination, delete by session)
- `CheckpointerStore` — LangGraph checkpoint persistence (`get_checkpointer`, `close_checkpointer`)

The unified `StorageBackend` protocol combines all three plus lifecycle methods (`initialize`, `close`, `health_check`). Swapping backends requires no changes to any code above Layer 2.

**Implementations** (`server/app/storage/`):

| Backend | Module | Use Case |
|---|---|---|
| `SqliteStorageBackend` | `sqlite.py` | Development, single-node |
| `PostgresStorageBackend` | `postgres.py` | Production, multi-node |
| `MemoryStorageBackend` | `memory.py` | Testing, ephemeral |

**`server/app/storage/factory.py`** — `create_storage_backend(settings)` reads `settings.persistence_backend` and returns the correct implementation. Unknown backend values raise `StorageBackendError` — there is no silent fallback to SQLite.

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

**`server/app/agent/sandbox_backend.py`** — Two Cognition-specific backends that wrap the execution layer:

- `CognitionLocalSandboxBackend` — Commands executed in the local process using `shlex.split()` + `shell=False`. Protected paths (`.cognition/` by default) block write operations. No `shell=True` anywhere.
- `CognitionDockerSandboxBackend` — File operations run directly on the host filesystem; command execution is routed through `DockerExecutionBackend`. Container is created lazily and reused within a session.

`create_sandbox_backend(settings)` selects between them based on `settings.sandbox_backend`.

---

### Layer 4 — Agent Runtime

The agent runtime is the brain of the system. It translates high-level `AgentDefinition` objects into running agents and normalizes all events into a single canonical stream.

**`server/app/agent/runtime.py`** — The `AgentRuntime` protocol:

```python
class AgentRuntime(Protocol):
    async def astream_events(self, message: str, ...) -> AsyncIterator[AgentEvent]: ...
    async def ainvoke(self, message: str, ...) -> AgentEvent: ...
    async def get_state(self) -> dict[str, Any]: ...
    async def abort(self) -> None: ...
    def get_checkpointer(self) -> BaseCheckpointSaver: ...
```

`DeepAgentRuntime` is the concrete implementation. It wraps Deep Agents, transforms its event stream into the 11 canonical `AgentEvent` subtypes, and handles abort via a thread-ID-based cancellation set.

**Canonical event types** emitted by every runtime:

| Event | Key Fields | Description |
|---|---|---|
| `TokenEvent` | `content` | A single streaming LLM token |
| `ToolCallEvent` | `name`, `args`, `id` | Agent invoking a tool |
| `ToolResultEvent` | `tool_call_id`, `output`, `exit_code` | Tool execution result |
| `PlanningEvent` | `todos` | Agent creating a task plan |
| `StepCompleteEvent` | `step_number`, `total_steps`, `description` | Plan step finished |
| `DelegationEvent` | `target_agent`, `task` | Primary agent delegating to subagent |
| `StatusEvent` | `status` | `thinking` or `idle` |
| `UsageEvent` | `input_tokens`, `output_tokens`, `estimated_cost` | Token accounting |
| `DoneEvent` | `assistant_data` | Stream complete |
| `ErrorEvent` | `message`, `code` | Recoverable error |

**`server/app/agent/definition.py`** — `AgentDefinition` is a Pydantic model that fully describes an agent:

```python
class AgentDefinition(BaseModel):
    name: str
    system_prompt: str | PromptConfig | None
    tools: list[str]          # dotted import paths
    skills: list[str]         # paths to SKILL.md files or directories
    memory: list[str]         # paths to AGENTS.md-style files
    subagents: list[SubagentDefinition]
    middleware: list[str | dict]
    config: AgentConfig       # provider, model, temperature, max_tokens
    mode: Literal["primary", "subagent", "all"]
    description: str | None
    hidden: bool
    native: bool
```

Definitions can be loaded from YAML files (`load_agent_definition`) or Markdown with YAML frontmatter (`load_agent_definition_from_markdown`). In the Markdown format, the frontmatter provides fields and the body becomes the `system_prompt`.

**`server/app/agent/agent_definition_registry.py`** — `AgentDefinitionRegistry` holds all known agents. It initializes with two built-in agents:

- `default` — Full-access coding agent, all tools enabled, mode `primary`
- `readonly` — Analysis-only agent, write and execute tools disabled, mode `primary`

User-defined agents are loaded from `.cognition/agents/*.md` and `.cognition/agents/*.yaml`. The registry exposes `get_all()`, `get(name)`, `primaries()` (agents that can own a session), `subagents()` (agents that can only be invoked by other agents), and `reload()`.

**`server/app/agent/cognition_agent.py`** — `create_cognition_agent()` is the async factory that instantiates a Deep Agent from an `AgentDefinition`. It attaches the sandbox backend, built-in tools (`BrowserTool`, `SearchTool`, `InspectPackageTool`), MCP tools, ConfigRegistry API-registered tools, and the middleware stack (`CognitionObservabilityMiddleware`, `CognitionStreamingMiddleware`, `ToolSecurityMiddleware`). It also receives `store=` (LangGraph `BaseStore`) and `context_schema=CognitionContext` to enable cross-thread memory scoped by user. Agent instances are cached by an MD5 key of the definition for efficient reuse across sessions.

---

### Layer 5 — LLM Provider

**`server/app/llm/model_catalog.py`** — `ModelCatalog` fetches and caches the models.dev catalog (configurable URL, default 1-hour TTL). Provides enriched model metadata — context windows, tool call support, pricing, modalities — for API responses and validation warnings. The catalog is enrichment only: if unreachable, endpoints degrade gracefully. A static mapping (`PROVIDER_TYPE_TO_CATALOG_SLUGS`) translates Cognition provider types to models.dev provider slugs.

**`server/app/llm/deep_agent_service.py`** — `DeepAgentStreamingService` is the per-session streaming coordinator. Provider resolution follows a strict priority chain:

1. `SessionConfig.provider_id` — exact `ProviderConfig` lookup from ConfigRegistry
2. `SessionConfig.provider` + `SessionConfig.model` — direct session override
3. `AgentDefinition.config.provider` + `.model` — per-agent definition override
4. First enabled `ProviderConfig` from ConfigRegistry (sorted by priority)

If no provider can be resolved, `LLMProviderConfigError` is raised with an actionable message. There is no fallback chain — if a provider fails, the error surfaces immediately. `_build_model()` uses LangChain's `init_chat_model()` to construct the model instance.

`SessionAgentManager` is the server-level singleton that manages one `DeepAgentStreamingService` per active session and routes abort signals to the right session.

**`server/app/llm/discovery.py`** — Deprecated. The `DiscoveryEngine` is replaced by `ModelCatalog` for model browsing and metadata.

---

### Layer 6 — API & Streaming

**`server/app/api/routes/`** — FastAPI route handlers for all resources. The routes do not contain business logic; they validate inputs, call into Layer 4 or Layer 2, and serialize outputs.

**`server/app/api/sse.py`** — `SSEStream` implements the SSE protocol with:
- Automatic reconnection support via `Last-Event-ID` header and `EventBuffer` replay
- Heartbeat comments (`:heartbeat`) sent every 15 seconds to keep proxies alive
- Sequential event IDs for ordering and gap detection
- `EventBuilder` static factory for every event type

**`server/app/api/scoping.py`** — `create_scope_dependency()` builds a FastAPI dependency that reads `x-cognition-scope-{key}` headers for each key in `settings.scope_keys`. When `scoping_enabled=true`, missing headers return `403 Forbidden` (fail-closed). Scope values are matched against session scopes to enforce tenant isolation.

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
1. Layer 2: Initialize storage backend
2. Layer 2: Initialize ConfigRegistry
3. Layer 1: Bootstrap providers from config.yaml (seed_if_absent)
4. Layer 4: Initialize agent definition registry
5. Layer 4: Seed agent definitions from ConfigRegistry
6. Layer 4: Initialize ConfigChangeDispatcher (hot-reload)
7. Layer 4: Initialize session manager
8. Layer 4: Initialize agent registry (tool/middleware auto-discovery)
9. Layer 4: Start file watcher for .cognition/tools/ hot-reload
10. Layer 7: Setup OTel tracing
11. Layer 7: Setup Prometheus metrics
12. Layer 7: Setup MLflow
13. Layer 6: Start rate limiter
```

Shutdown happens in reverse: stop watcher → stop rate limiter → close storage.

---

## The North Star

The architectural goal is a single declarative entry point:

```python
from cognition import AgentDefinition, Cognition

agent = AgentDefinition(
    tools=[my_tool, another_tool],
    skills=[".cognition/skills/deploy-app/"],
    system_prompt="You are a deployment expert.",
)

app = Cognition(agent)
app.run()
```

This one call should provision the full 7-layer stack: REST API, SSE streaming, SQLite/Postgres persistence, local/Docker sandbox, OTel tracing, Prometheus metrics, multi-tenant scoping, rate limiting, and an evaluation pipeline. All layers, all infrastructure, from a single agent definition.
