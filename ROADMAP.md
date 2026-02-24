# Cognition Roadmap

This roadmap is derived from [FIRST-PRINCIPLE-EVALUTION.md](./FIRST-PRINCIPLE-EVALUTION.md), the architectural source of truth for Cognition. All development must move the system toward the 7-layer architecture defined in that document.

**Governance:** This file must be updated before starting major work, merging architectural changes, or changing priority direction. See [AGENTS.md](./AGENTS.md) for enforcement rules.

**Priority rules:**
- P0 blocks P1. P1 blocks P2. P2 blocks P3.
- Security fixes override all priorities.
- Architecture corrections override feature work.

---

## Current State

| Priority | Tasks | Complete | Status |
|----------|-------|----------|--------|
| **P0** (Table Stakes) | 6 | 6/6 | **100% ✅** |
| **P1** (Production Ready) | 6 | 6/6 | **100% ✅** |
| **P2** (Robustness + GUI) | 16 | 16/16 | **100% ✅** |
| — Cleanup (LangGraph Alignment) | 5 | 5/5 | ✅ Complete |
| — Robustness | 7 | 7/7 | ✅ Complete |
| — GUI Extensibility | 4 | 4/4 | ✅ Complete |
| **P3** (Multi-Agent Registry) | 4 | 0/4 | **0% — Active** |
| **P4** (Extended Vision) | 2 | 0/2 | **0% — Deferred** |

**Unit tests:** 263 passed, 4 skipped, 1 warning
**E2E Business Scenarios:** 16/16 scenarios passing across P2 Cleanup, Robustness, and GUI Extensibility
**Live tests:** 41/41 pass across 9 phases (MLflow tracing fixed via OTel Collector → MLflow v3.10.0)
**API Proof Script:** 45/45 assertions pass across 9 scenarios with session scoping enabled

---

## P0 — Table Stakes ✅ 100% Complete

All table stakes resolved. No blockers.

### P0-1: Message Persistence ✅

| Field | Value |
|-------|-------|
| **Layer** | 2 (Persistence) |
| **File** | `server/app/message_store.py` |

SQLite-backed message storage with full CRUD, pagination, and session scoping. 8 unit tests.

### P0-2: Remove `shell=True` ✅

| Field | Value |
|-------|-------|
| **Layer** | 3 (Execution) |
| **File** | `server/app/execution/sandbox.py` |

Commands parsed with `shlex.split()`. Shell metacharacters are not interpreted.

### P0-3: Session Scoping Harness ✅

| Field | Value |
|-------|-------|
| **Layer** | 6 (API & Streaming) |
| **File** | `server/app/api/scoping.py` |

Generic composable scoping via `X-Cognition-Scope-{Key}` headers. Multi-dimensional support. Fail-closed when enabled. 19 unit tests.

**Enabled by default in docker-compose** with `COGNITION_SCOPING_ENABLED=true` and `COGNITION_SCOPE_KEYS=["user"]`.

### P0-4: Wire Rate Limiter ✅

| Field | Value |
|-------|-------|
| **Layer** | 6 (API & Streaming) |
| **File** | `server/app/rate_limiter.py` |

Token bucket rate limiter applied to message routes. Scope-aware keys. 429 responses.

### P0-5: Functional Abort ✅

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) / 6 (API) |
| **File** | `server/app/api/routes/sessions.py` |

Abort endpoint cancels active streaming task. Session remains usable. 5 unit tests.

### P0-6: Observability Configuration ✅

| Field | Value |
|-------|-------|
| **Layer** | 7 (Observability) |
| **Files** | `server/app/observability/__init__.py`, `server/app/observability/mlflow_config.py`, `docker/otel-collector-config.yml` |

**Architecture:**
- **OpenTelemetry Collector** (`otel-collector` service) receives traces from Cognition
- **OTel → MLflow**: Collector exports traces to MLflow's `/v1/traces` endpoint
- **MLflow Config** (`mlflow_config.py`): Experiment setup, tracking URI configuration, availability checks
- **Grafana**: Centralized observability (metrics, logs, traces via Tempo)

**Key changes:**
- ✅ Replaced Jaeger with OTel Collector as central trace collector
- ✅ MLflow v3.10.0 server for trace storage and evaluation
- ✅ Renamed `mlflow_tracing.py` → `mlflow_config.py` (clearer separation of concerns)
- ✅ OTel/MLflow gating via settings toggles
- ✅ Graceful degradation when MLflow is disabled
- ✅ 12 unit tests

**Trace flow:**
```
Cognition → OTel Collector (gRPC:4317) → MLflow /v1/traces → PostgreSQL (trace_info, spans tables)
                                    ↘ Grafana Tempo (future)
```

---

## P1 — Production Ready ✅ 100% Complete

All production-ready features implemented.

### P1-1: Unified StorageBackend Protocol ✅

| Field | Value |
|-------|-------|
| **Layer** | 2 (Persistence) |
| **Files** | `server/app/storage/backend.py`, `server/app/storage/factory.py`, `server/app/storage/sqlite.py` |

`StorageBackend` protocol with sub-interfaces: `SessionStore`, `MessageStore`, `Checkpointer`. SQLite implementation with connection pooling.

### P1-2: Postgres Support ✅

| Field | Value |
|-------|-------|
| **Layer** | 2 (Persistence) |
| **File** | `server/app/storage/postgres.py` |

Full PostgreSQL backend with asyncpg. Factory dispatches correctly. Docker Compose includes Postgres service.

### P1-3: Alembic Migrations ✅

| Field | Value |
|-------|-------|
| **Layer** | 2 (Persistence) |
| **File** | `server/alembic/` |

Async Alembic with CLI commands (`cognition db upgrade`, `cognition db migrate`). Works with SQLite and Postgres.

### P1-4: Docker Per-Session Sandbox ✅

| Field | Value |
|-------|-------|
| **Layer** | 3 (Execution) |
| **Files** | `server/app/execution/backend.py`, `server/app/agent/sandbox_backend.py`, `Dockerfile.sandbox` |

**Implementation:**
- `DockerExecutionBackend` with full container lifecycle (create/exec/stop/remove)
- `CognitionDockerSandboxBackend` combining `FilesystemBackend` (file ops) + `DockerExecutionBackend` (command execution)
- Settings-driven dispatch: `create_sandbox_backend()` factory reads `settings.sandbox_backend`
- `create_cognition_agent()` uses the factory — no hardcoded backend

**Security hardening:**
- `cap_drop=["ALL"]` — all Linux capabilities dropped
- `security_opt=["no-new-privileges"]` — prevents privilege escalation
- `read_only=True` — read-only root filesystem
- `tmpfs` for `/tmp` (64MB) and `/home` (16MB)
- Configurable resource limits: memory, CPU quota
- Configurable network isolation

**Deployment model:**
- **Local development (recommended):** Cognition runs on host, Docker sandbox provides real isolation. No socket hacks needed.
- **Docker Compose (testing/CI):** Uses `COGNITION_SANDBOX_BACKEND=local` — commands execute inside the Cognition container without isolation. Suitable for rapid testing only.

**Verified via live testing:** Container spawns in ~800ms cold / ~130ms warm. `whoami` returns `sandbox` user, `pwd` returns `/workspace`.

### P1-5: Declarative AgentDefinition ✅

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) / 4 (Agent Runtime) |
| **File** | `server/app/agent/definition.py` |

Pydantic V2 `AgentDefinition` model. YAML loading from `.cognition/agent.yaml`. Validation for tools and skills.

### P1-6: AgentRuntime Protocol ✅

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent/runtime.py` |

Cognition-owned `AgentRuntime` protocol wrapping Deep Agents. Methods: `astream_events()`, `ainvoke()`, `get_state()`, `abort()`.

---

## P2 — Robustness + GUI Extensibility (~45% Complete)

This priority tier combines robustness improvements, technical debt cleanup, and new extensibility APIs for GUI applications. A key theme is **aligning with LangGraph/Deep Agents primitives** — removing custom code that duplicates what the framework already provides, and building only what LangGraph cannot.

### Deep Agents / LangGraph Alignment

Before building new features, the following cleanup is required to eliminate duplication and simplify the architecture. Always prefer **Deep Agents** primitives (the higher-level abstraction Cognition is built on) over raw LangGraph/LangChain where available.

**Deep Agents provides:**
- `create_deep_agent()` — agent factory with `tools`, `middleware`, `skills`, `memory`, `subagents`, `backend`, `context_schema`, `checkpointer`, `store`
- **Middleware:** `FilesystemMiddleware`, `MemoryMiddleware`, `SkillsMiddleware`, `SubAgentMiddleware`, `SummarizationMiddleware`
- **Backends:** `BackendProtocol`, `SandboxBackendProtocol` with 6 implementations (`StateBackend`, `StoreBackend`, `FilesystemBackend`, `LocalShellBackend`, `CompositeBackend`, `BaseSandbox`)

**LangGraph provides (lower level):**
- `thread_id`-based sessions, checkpointers, `Store` for cross-thread memory, `Runtime` context

**LangChain provides:**
- `AgentMiddleware` protocol (the base class for all middleware)

#### P2-CLEANUP-1: Remove Legacy `persistence/` Package ✅

| Field | Value |
|-------|-------|
| **Layer** | 2 (Persistence) |
| **Files removed** | `server/app/persistence/base.py`, `memory.py`, `sqlite.py`, `factory.py` |
| **Status** | **Complete** |

Removed `server/app/persistence/` package (155 lines deleted). `StorageBackend` is now the single persistence abstraction.

#### P2-CLEANUP-2: Wire Unified StorageBackend, Remove Standalone Stores ✅

| Field | Value |
|-------|-------|
| **Layer** | 2 (Persistence) / 6 (API) |
| **Files removed** | `server/app/session_store.py`, `server/app/message_store.py` |
| **Status** | **Complete** |

Deleted standalone stores (630 lines deleted). `StorageBackend` is now wired into API routes via `get_storage_backend()` accessor. Custom messages table kept for API/query needs.

#### P2-CLEANUP-3: Consolidate Event Types ✅

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) / 6 (API) |
| **Files affected** | `deep_agent_service.py`, `agent/runtime.py`, `api/models.py` |
| **Status** | **Complete** |

Event types consolidated in `agent/runtime.py` as canonical source. API layer serializes from canonical types.

#### P2-CLEANUP-4: Replace ContextManager Memory Stubs ✅

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent/context.py` |
| **Status** | **Complete** |

Removed `save_memory()` / `load_memory()` stubs (25 lines deleted). Deep Agents `MemoryMiddleware` handles AGENTS.md. LangGraph `Store` available via `create_deep_agent(store=...)` when needed.

#### P2-CLEANUP-5: Cache Compiled Agents Per Session ✅

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/llm/deep_agent_service.py` |
| **Status** | **Complete** |

Implemented agent caching with MD5-based cache keys. Recompilation only triggered when tool/middleware configuration changes.

---

### Existing Robustness Items

### P2-1: SSE Reconnection ✅

| Field | Value |
|-------|-------|
| **Layer** | 6 (API & Streaming) |
| **File** | `server/app/api/sse.py` |

Event IDs (`{counter}-{uuid}`), `retry:` directive (3000ms), keepalive heartbeat (15s), Last-Event-ID resume, circular event buffer. 38 unit tests.

### P2-2: Circuit Breaker ✅

| Field | Value |
|-------|-------|
| **Layer** | 5 (LLM Provider) |
| **File** | `server/app/execution/circuit_breaker.py` |
| **Status** | **Complete** |

Wired `CircuitBreaker` into `ProviderFallbackChain` with retry logic and health endpoint integration. Circuit breaker state exposed via `/health` endpoint.

### P2-3: Evaluation Pipeline ✅

| Field | Value |
|-------|-------|
| **Layer** | 7 (Observability) |
| **File** | `server/app/evaluation/workflows.py` |

`EvaluationService` with 3 built-in scorers (ToolEfficiency, SafetyCompliance, ResponseQuality), feedback collection, MLflow metric logging (when enabled), dataset creation from feedback. In-memory feedback storage.

### P2-4: CORS Middleware ✅

| Field | Value |
|-------|-------|
| **Layer** | 6 (API & Streaming) |
| **File** | `server/app/main.py` (lines 62-70) |

Settings-driven CORS: `cors_origins`, `cors_methods`, `cors_headers`, `cors_credentials`.

### P2-5: Enrich Message Model ✅

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) |
| **File** | `server/app/models.py` |

`Message` model includes: `tool_calls` (list of `ToolCall`), `tool_call_id`, `token_count`, `model_used`, `metadata`.

### P2-6: ExecutionBackend Protocol ✅

| Field | Value |
|-------|-------|
| **Layer** | 3 (Execution) |
| **File** | `server/app/execution/backend.py` |

`ExecutionBackend` runtime-checkable Protocol with `execute()`, `read_file()`, `write_file()`, `list_files()`. Local and Docker implementations. `ExecutionBackendAdapter` bridges to `deepagents.SandboxBackendProtocol`. Factory dispatches on config.

### P2-7: Integrate ContextManager ✅

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent/context.py` |

`ContextManager` wired into `cognition_agent.py`. Project indexing, file relevance scoring, context pruning.

---

### New GUI Extensibility Items

### P2-8: SessionManager ✅

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) / 6 (API) |
| **File** | `server/app/session_manager.py` |
| **Status** | **Complete** |

Application-level session management for GUI applications. Wraps `StorageBackend` + Deep Agents. Provides session lifecycle events, cross-workspace queries, and per-session agent instances with different tool/middleware stacks.

**Key Features:**
- Session lifecycle events (`on_session_created`, `on_session_deleted`) for GUI callbacks
- Cross-workspace session queries
- Automatic agent caching per session
- LangGraph `thread_id` as session identifier
- Deep Agents `context_schema` for user/org scoping

### P2-9: AgentRegistry ✅

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent_registry.py` |
| **Status** | **Complete** |

Dynamic tool/middleware registration for GUI applications. Provides per-session tool/middleware management with auto-discovery from `.cognition/tools/` and `.cognition/middleware/` directories.

**Key Features:**
- Factory pattern for fresh tool/middleware instances per session
- Auto-discovery from `.cognition/tools/` and `.cognition/middleware/`
- Hot-reload: Tools reload immediately, middleware session-based
- `AgentRegistry.reload_tools()` for immediate tool updates
- `AgentRegistry.mark_middleware_pending()` for session-based middleware updates

### P2-10: File Watcher & Hot-Reload API ✅

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) |
| **File** | `server/app/file_watcher.py` |
| **Dependencies** | watchdog library, P2-9 (AgentRegistry) |
| **Status** | **Complete** |

File system watcher for workspace changes with hot-reload support. Integrates with AgentRegistry to trigger reloads when files change.

**Key Features:**
- `WorkspaceWatcher` class monitoring `.cognition/tools/`, `.cognition/middleware/`, and `config.yaml`
- Debounced change processing (configurable delay)
- GUI callbacks: `on_tools_changed`, `on_middleware_changed`, `on_config_changed`
- Tools reload immediately, middleware session-based
- `SimpleFileWatcher` for basic use cases

### P2-11: CLI Tool/Middleware Scaffolding ✅

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) |
| **File** | `server/app/cli.py` |
| **Status** | **Complete** |

CLI commands for creating tool and middleware templates with proper structure.

**Commands:**
- `cognition create tool <name>` — generates `.cognition/tools/{name}.py` with `@tool` decorator
- `cognition create middleware <name>` — generates `.cognition/middleware/{name}.py` with `AgentMiddleware`
- Automatic directory creation
- Templates include proper imports and structure

---

## P3 — Full Vision (0% Complete)

Multi-agent registry, per-agent configuration, and agent-scoped sessions. This is the next active priority.

**Design principle:** Leverage Deep Agents' native `SubAgent` TypedDict and `create_deep_agent(subagents=[...])` as the compilation primitive. Cognition owns only the registry, YAML/Markdown loading, translation layer, storage, and API surface. No custom routing logic.

**Deep Agents integration model:**
- Primary agents → compiled via `create_deep_agent(system_prompt=..., subagents=[...])`
- Subagent-mode agents → translated to `SubAgent` TypedDicts, passed into the primary agent's `create_deep_agent()` call; routing handled automatically by Deep Agents' injected `task` tool
- Per-agent model, temperature, skills overrides → mapped directly to `SubAgent.model`, `SubAgent.skills`, etc.

---

### P3-1: `AgentDefinitionRegistry` + Built-in Agents

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent/agent_registry.py` |
| **Status** | Not Started |
| **Effort** | ~3–4 days |
| **Dependencies** | P1-5 (`AgentDefinition` model) ✅ |

A catalog of all known agents: built-in (hardcoded, `native=True`) and user-defined (loaded from `.cognition/agents/`). Merges both sources at startup and on reload.

**Built-in agents shipped with the server:**

| Name | Mode | Description |
|------|------|-------------|
| `default` | `primary` | Full-access coding agent; uses the existing `SYSTEM_PROMPT` constant |
| `readonly` | `primary` | Analysis-only agent; write/edit/execute tools disabled via `interrupt_on`; useful for CI, code review |

**`AgentDefinitionRegistry` interface:**
```python
class AgentDefinitionRegistry:
    def list(self, include_hidden: bool = False) -> list[AgentDefinition]: ...
    def get(self, name: str) -> AgentDefinition | None: ...
    def reload(self) -> None: ...  # re-scans .cognition/agents/
    def subagents(self) -> list[AgentDefinition]: ...  # mode in ("subagent", "all")
    def primaries(self) -> list[AgentDefinition]: ...  # mode in ("primary", "all")
```

**Global singleton helpers:** `get_agent_definition_registry()`, `initialize_agent_definition_registry(workspace_path)`.

**Acceptance Criteria:**
- [ ] Built-in `default` and `readonly` agents present in every registry instance
- [ ] `initialize_agent_definition_registry(workspace_path)` scans `.cognition/agents/` and merges user-defined agents
- [ ] User agents override built-in agents when names collide (user wins)
- [ ] `reload()` rescans without server restart
- [ ] Unit tests covering: list, get, reload, built-ins present, user override

---

### P3-2: Markdown + YAML Agent File Loading

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent/definition.py` |
| **Status** | Not Started |
| **Effort** | ~2 days |
| **Dependencies** | P3-1 |

Extend `AgentDefinition` with registry-facing fields and add a Markdown loader so users can define agents using the OpenCode-compatible format.

**New fields on `AgentDefinition`:**
```python
mode: Literal["primary", "subagent", "all"] = "all"
description: str | None = None
hidden: bool = False
native: bool = False   # True = built-in; set by registry, not user config
```

**Markdown format** (`.cognition/agents/review.md`):
```markdown
---
description: Reviews code for best practices and security issues
mode: subagent
model: anthropic/claude-haiku-4
temperature: 0.1
skills:
  - .cognition/skills/
---
You are a code reviewer. Focus on security, performance, and maintainability.
Provide constructive feedback without making direct changes.
```
- Filename stem becomes `name` (e.g., `review.md` → `name="review"`)
- YAML frontmatter maps to `AgentDefinition` fields
- Body becomes `system_prompt`

**YAML format** (`.cognition/agents/security-auditor.yaml`) — already supported by `load_agent_definition()`; just needs the new fields added.

**New loader function:** `load_agent_definition_from_markdown(path) -> AgentDefinition`

**Translation to Deep Agents `SubAgent` TypedDict:**
```python
def to_subagent(self) -> SubAgent:
    spec: SubAgent = {
        "name": self.name,
        "description": self.description or "",
        "system_prompt": self.system_prompt,
    }
    if self.config.model:
        provider = self.config.provider
        spec["model"] = f"{provider}:{self.config.model}" if provider else self.config.model
    if self.skills:
        spec["skills"] = self.skills
    return spec
```

**Acceptance Criteria:**
- [ ] `load_agent_definition_from_markdown(path)` parses frontmatter + body correctly
- [ ] `AgentDefinition.to_subagent()` returns a valid `SubAgent` TypedDict
- [ ] `mode`, `description`, `hidden` fields validated by Pydantic
- [ ] `native` field is read-only (cannot be set via YAML/Markdown; only set by registry code)
- [ ] Unit tests: markdown roundtrip, YAML roundtrip, `to_subagent()` translation, validation errors

---

### P3-3: Session–Agent Binding + Storage

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) + 2 (Persistence) + 4 (Agent Runtime) |
| **Files** | `server/app/models.py`, `server/app/storage/schema.py`, `server/app/storage/sqlite.py`, `server/app/storage/postgres.py`, `server/app/llm/deep_agent_service.py` |
| **Status** | Not Started |
| **Effort** | ~3 days |
| **Dependencies** | P3-1, P3-2 |

Bind each session to a named agent at creation time. The selected primary agent is compiled with all registered subagent-mode agents available via the `task` tool.

**`Session` domain model change:**
```python
@dataclass
class Session:
    ...
    agent_name: str = "default"
```

**Storage schema change:** `agent_name TEXT NOT NULL DEFAULT 'default'` added to the sessions table. `to_dict()` / `from_dict()` updated. Alembic migration added.

**Agent compilation change in `DeepAgentStreamingService.stream_response()`:**
```python
# Before: always uses global settings
agent = create_cognition_agent(project_path, model, checkpointer, settings)

# After: looks up definition, passes subagents from registry
definition = registry.get(session.agent_name)
subagents = [d.to_subagent() for d in registry.subagents() if d.name != session.agent_name]
agent = create_cognition_agent(
    project_path, model, checkpointer, settings,
    system_prompt=definition.system_prompt,
    subagents=subagents,
)
```

**Acceptance Criteria:**
- [ ] `Session.agent_name` persisted and loaded correctly from SQLite and Postgres
- [ ] Sessions with unknown `agent_name` fall back to `"default"` on load (resilience)
- [ ] Each session's primary agent is compiled with all `subagent`/`all`-mode agents from the registry available as subagents
- [ ] Agent cache key includes `agent_name` so different agents don't share compiled instances
- [ ] Unit tests: session persistence roundtrip, compilation path, cache keying

---

### P3-4: `GET /agents` API Endpoint

| Field | Value |
|-------|-------|
| **Layer** | 6 (API & Streaming) |
| **Files** | `server/app/api/routes/agents.py`, `server/app/api/models.py`, `server/app/api/routes/__init__.py` |
| **Status** | Not Started |
| **Effort** | ~2 days |
| **Dependencies** | P3-1, P3-2, P3-3 |

Expose the agent registry via the API. Extend session creation to accept `agent_name`.

**New endpoints:**
```
GET  /agents           — list all non-hidden agents
GET  /agents/{name}    — get single agent (404 if not found or hidden)
```

**Response model:**
```python
class AgentResponse(BaseModel):
    name: str
    description: str | None
    mode: Literal["primary", "subagent", "all"]
    hidden: bool
    native: bool
    model: str | None       # per-agent model override if set
    temperature: float | None

class AgentList(BaseModel):
    agents: list[AgentResponse]
```

**`SessionCreate` change:**
```python
class SessionCreate(BaseModel):
    title: str | None = None
    agent_name: str | None = None   # if omitted → "default"
```

Session creation validates that `agent_name` resolves to a known, non-hidden, `primary`/`all`-mode agent. Returns `422` with a clear error if not.

**Scope:** Hidden agents (`hidden=True`) are excluded from `GET /agents` entirely. They remain accessible by exact name for internal use (e.g., by other agents via the `task` tool).

**Acceptance Criteria:**
- [ ] `GET /agents` returns all non-hidden agents with correct fields
- [ ] `GET /agents/{name}` returns 404 for unknown or hidden agents
- [ ] `POST /sessions` with valid `agent_name` creates session bound to that agent
- [ ] `POST /sessions` with invalid/hidden `agent_name` returns 422
- [ ] `POST /sessions` without `agent_name` defaults to `"default"`
- [ ] `GET /sessions/{id}` response includes `agent_name`
- [ ] Unit tests for all endpoint cases; integration test for full session → agent compilation path

---

## P4 — Extended Vision (0% Complete)

Deferred from P3. Unblocked after P3 is complete.

### P4-1: Cloud Execution Backends

| Field | Value |
|-------|-------|
| **Layer** | 3 (Execution) |
| **Status** | Not Started |
| **Effort** | ~2-4 weeks per backend |
| **Dependencies** | P2-6 (ExecutionBackend Protocol) ✅ |

**Acceptance Criteria:**
- [ ] At least one cloud backend (ECS or Lambda) implementing `ExecutionBackend`
- [ ] Container image registry integration
- [ ] Auto-scaling based on session demand
- [ ] Cost-aware scheduling
- [ ] Configuration-driven backend selection

### P4-2: Ollama Provider + LLM Resilience

| Field | Value |
|-------|-------|
| **Layer** | 5 (LLM Provider) |
| **Status** | Not Started |
| **Effort** | ~1-2 weeks |
| **Dependencies** | P2-2 (Circuit Breaker) ✅ |

**Acceptance Criteria:**
- [ ] Ollama provider factory registered
- [ ] Streaming-level fallback: mid-stream provider failure triggers fallback
- [ ] Provider factories return typed protocol, not `Any`
- [ ] Gateway integration option (MLflow AI Gateway or LiteLLM)

---

## Codebase Health

### File Organization

All modules are in their correct architectural layer:

| Layer | Directory | Contents |
|-------|-----------|----------|
| L1 Foundation | `server/app/models.py`, `server/app/exceptions.py`, `server/app/settings.py` | Domain models, exceptions, config |
| L2 Persistence | `server/app/storage/`, `server/app/session_store.py`, `server/app/message_store.py` | Storage backends, SQLite/Postgres |
| L3 Execution | `server/app/execution/` | `sandbox.py`, `backend.py`, `circuit_breaker.py` |
| L4 Agent Runtime | `server/app/agent/` | `cognition_agent.py`, `sandbox_backend.py`, `definition.py`, `runtime.py`, `context.py`, `prompt_registry.py` |
| L5 LLM Provider | `server/app/llm/` | Provider registry, fallback chain, discovery, mock |
| L6 API & Streaming | `server/app/api/` | Routes, SSE, models, `scoping.py`, `middleware.py` |
| L7 Observability | `server/app/observability/` | OTel setup, `mlflow_tracing.py` |

### Testing Status

- **Unit tests:** 223 passed, 2 skipped, 1 warning
- **Live tests:** 40/41 across 9 phases
  - Phase 1 (Service Health): 9/9
  - Phase 2 (Core API CRUD): 6/6
  - Phase 3 (Agent Streaming): 6/6
  - Phase 4 (MLflow Observability): 2/3 (upstream ContextVar bug)
  - Phase 5 (Docker Sandbox): 5/5
  - Phase 6 (Persistence Across Restart): 4/4
  - Phase 7 (Prometheus Metrics): 3/3
  - Phase 8 (Distributed Tracing): 2/2
  - Phase 9 (Security & Resilience): 3/3
- **API Proof Script:** 45/45 assertions across 9 scenarios
  - `scripts/test_docker_compose.sh` - Comprehensive bash script testing all 12 API endpoints
  - Tests session scoping, SSE streaming, CRUD operations, multi-turn conversations
  - Color-coded output with pass/fail reporting
  - CI-friendly with non-zero exit on failures

### Known Issues

| Issue | Severity | Status |
|-------|----------|--------|
| MLflow `autolog()` ContextVar error in async contexts | Medium | Upstream MLflow bug. `run_tracer_inline=True` applied but insufficient. OTel tracing works as alternative. |
| Prompt registry not wired into agent factory | Low | Implementation exists, integration pending (GUITool was removed) |
| Evaluation feedback stored in-memory only | Low | Needs persistence backend (P3-1) |

### New Testing Tools

| Tool | Purpose | Location |
|------|---------|----------|
| API Proof Script | End-to-end API testing with docker-compose | `scripts/test_docker_compose.sh` |
| | - Tests all 12 API endpoints | |
| | - 9 scenarios covering health, CRUD, SSE, scoping, observability | |
| | - Self-contained (requires only curl + jq) | |
| | - Supports session scoping with automatic header injection | |

---

## Next Steps

**P2 Complete!** All robustness and GUI extensibility items finished. Session scoping enabled and tested.

### Recently Completed
- ✅ API Proof Script (`scripts/test_docker_compose.sh`) - 45/45 assertions passing
- ✅ Session scoping enabled in docker-compose with multi-tenant isolation
- ✅ All CircuitBreaker methods implemented and wired
- ✅ PostgreSQL checkpointer using psycopg for LangGraph
- ✅ Storage backend fixes for scopes and message columns

**Immediate — P3 Multi-Agent Registry:**
1. P3-1: `AgentDefinitionRegistry` + built-in `default` and `readonly` agents (~3–4 days)
2. P3-2: Markdown + YAML agent file loading + `AgentDefinition.to_subagent()` (~2 days)
3. P3-3: Session–agent binding + storage migration (~3 days)
4. P3-4: `GET /agents` endpoint + `agent_name` on session creation (~2 days)

**Deferred to P4:**
- Cloud execution backends (P4-1)
- Ollama provider + LLM resilience (P4-2)

---

## Architectural North Star

All work converges toward:

```python
agent = AgentDefinition(
    tools=[...],
    skills=[...],
    system_prompt="...",
)

app = Cognition(agent)
app.run()
```

Automatically providing: REST API, SSE streaming, persistence, sandbox isolation, observability, multi-user scoping, and evaluation pipeline.
