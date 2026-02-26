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
| **P3** (Multi-Agent Registry) | 4 | 4/4 | **100% ✅** |
| **P3-ALN** (Deep Agents Alignment) | 3 | 3/3 | **100% ✅** |
| **P3-SEC** (Security Hardening) | 5 | 5/5 | **100% ✅** |
| **P3-TR** (Tool Registry End-to-End) | 9 | 9/9 | **100% ✅** |
| **P4** (Extended Vision) | 2 | 0/2 | **0% — Deferred** |

**Unit tests:** 338 passed, 4 skipped, 2 warnings
**E2E Business Scenarios:** 29/29 scenarios passing across P2 Cleanup, Robustness, GUI Extensibility, and P3 Multi-Agent Registry
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

## P2 — Robustness + GUI Extensibility ✅ 100% Complete

This priority tier combines robustness improvements, technical debt cleanup, and new extensibility APIs for GUI applications. A key theme is **aligning with LangGraph/Deep Agents primitives** — removing custom code that duplicates what the framework already provides, and building only what LangGraph cannot.

All P2 items complete: Cleanup (5/5), Robustness (7/7), GUI Extensibility (4/4).

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

## P3 — Multi-Agent Registry ✅ 100% Complete

Multi-agent registry system with built-in and user-defined agents, session-agent binding, and Deep Agents native subagent support.

**Design principle:** Leverage Deep Agents' native `SubAgent` TypedDict and `create_deep_agent(subagents=[...])` as the compilation primitive. Cognition owns only the registry, YAML/Markdown loading, translation layer, storage, and API surface. No custom routing logic.

**Deep Agents integration model:**
- Primary agents → compiled via `create_deep_agent(system_prompt=..., subagents=[...])`
- Subagent-mode agents → translated to `SubAgent` TypedDicts, passed into the primary agent's `create_deep_agent()` call; routing handled automatically by Deep Agents' injected `task` tool
- Per-agent model, temperature, skills overrides → mapped directly to `SubAgent.model`, `SubAgent.skills`, etc.

---

### P3-1: `AgentDefinitionRegistry` + Built-in Agents ✅

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent/agent_definition_registry.py` |
| **Status** | **Complete** |
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
    def is_valid_primary(self, name: str) -> bool: ...  # validates for session creation
```

**Global singleton helpers:** `get_agent_definition_registry()`, `initialize_agent_definition_registry(workspace_path, settings)`.

**Acceptance Criteria:**
- [x] Built-in `default` and `readonly` agents present in every registry instance
- [x] `initialize_agent_definition_registry(workspace_path, settings)` scans `.cognition/agents/` and merges user-defined agents
- [x] User agents override built-in agents when names collide (user wins)
- [x] `reload()` rescans without server restart
- [x] `is_valid_primary()` prevents subagent-mode agents from being used as session primaries
- [x] Unit tests covering: list, get, reload, built-ins present, user override, primary validation

---

### P3-2: Markdown + YAML Agent File Loading ✅

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent/definition.py` |
| **Status** | **Complete** |
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

**Markdown format** (`.cognition/agents/researcher.md`):
```markdown
---
name: researcher
description: A specialized research agent for gathering information
mode: subagent
---

You are a specialized research agent focused on gathering and synthesizing information.
You should search for relevant information, analyze sources, and provide comprehensive summaries.
```
- Filename stem becomes `name` (e.g., `researcher.md` → `name="researcher"`)
- YAML frontmatter maps to `AgentDefinition` fields
- Body becomes `system_prompt`

**YAML format** (`.cognition/agents/security-auditor.yaml`) — already supported by `load_agent_definition()`; now includes the new fields.

**New loader function:** `load_agent_definition_from_markdown(path) -> AgentDefinition`

**Translation to Deep Agents `SubAgent` TypedDict:**
```python
def to_subagent(self) -> SubAgent:
    """Translate to Deep Agents SubAgent TypedDict."""
    spec: SubAgent = {
        "name": self.name,
        "description": self.description or "",
        "system_prompt": self.system_prompt,
    }
    if self.config.model:
        spec["model"] = self.config.model
    if self.skills:
        spec["skills"] = self.skills
    return spec
```

**Acceptance Criteria:**
- [x] `load_agent_definition_from_markdown(path)` parses frontmatter + body correctly
- [x] `AgentDefinition.to_subagent()` returns a valid `SubAgent` TypedDict
- [x] `mode`, `description`, `hidden` fields validated by Pydantic
- [x] `native` field is read-only (set by registry code only)
- [x] Unit tests: markdown roundtrip, YAML roundtrip, `to_subagent()` translation, validation errors

---

### P3-3: Session–Agent Binding + Storage ✅

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) + 2 (Persistence) + 4 (Agent Runtime) |
| **Files** | `server/app/models.py`, `server/app/storage/schema.py`, `server/app/storage/sqlite.py`, `server/app/storage/postgres.py`, `server/app/llm/deep_agent_service.py` |
| **Status** | **Complete** |
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

**Storage schema change:** `agent_name TEXT NOT NULL DEFAULT 'default'` added to the sessions table. `to_dict()` / `from_dict()` updated. Alembic migration added (`003_add_agent_name.py`).

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
- [x] `Session.agent_name` persisted and loaded correctly from SQLite and Postgres
- [x] Sessions with unknown `agent_name` fall back to `"default"` on load (resilience)
- [x] Each session's primary agent is compiled with all `subagent`/`all`-mode agents from the registry available as subagents
- [x] Agent cache key includes `agent_name` so different agents don't share compiled instances
- [x] Alembic migration works for both SQLite and PostgreSQL
- [x] Unit tests: session persistence roundtrip, compilation path, cache keying

---

### P3-4: `GET /agents` API Endpoint ✅

| Field | Value |
|-------|-------|
| **Layer** | 6 (API & Streaming) |
| **Files** | `server/app/api/routes/agents.py`, `server/app/api/models.py`, `server/app/api/routes/__init__.py`, `server/app/api/routes/sessions.py` |
| **Status** | **Complete** |
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

Session creation validates that `agent_name` resolves to a known, non-hidden, `primary`/`all`-mode agent using `is_valid_primary()`. Returns `422` with a clear error if not.

**Scope:** Hidden agents (`hidden=True`) are excluded from `GET /agents` entirely. They remain accessible by exact name for internal use (e.g., by other agents via the `task` tool).

**Acceptance Criteria:**
- [x] `GET /agents` returns all non-hidden agents with correct fields
- [x] `GET /agents/{name}` returns 404 for unknown or hidden agents
- [x] `POST /sessions` with valid `agent_name` creates session bound to that agent
- [x] `POST /sessions` with invalid/hidden `agent_name` returns 422
- [x] `POST /sessions` with subagent-mode `agent_name` returns 422
- [x] `POST /sessions` without `agent_name` defaults to `"default"`
- [x] `GET /sessions/{id}` response includes `agent_name`
- [x] Unit tests for all endpoint cases; integration test for full session → agent compilation path

---

## P3-ALN — Deep Agents Alignment ✅ 100% Complete

**Architecture corrections override feature work** (AGENTS.md governance rule). These items were identified by auditing the actual `deepagents==0.3.12` package source against Cognition's current implementation. Each item removes duplication or corrects a regression introduced by reimplementing something the library already provides correctly.

**All three items complete.**

---

### P3-ALN-1: Replace `CognitionLocalSandboxBackend` with a thin subclass of `LocalShellBackend`

| Field | Value |
|-------|-------|
| **Layer** | 3 (Execution) |
| **Files** | `server/app/agent/sandbox_backend.py`, `server/app/execution/sandbox.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |

**Problem:** `CognitionLocalSandboxBackend` (`sandbox_backend.py:27-87`) is a near-complete reimplementation of `deepagents.backends.LocalShellBackend`. Both subclass `FilesystemBackend`, both implement `SandboxBackendProtocol`, both truncate at 100KB. However, the Cognition version introduces two regressions versus the upstream:

1. **Overrides `_resolve_path()` and removes `virtual_mode` support.** deepagents' `FilesystemBackend._resolve_path()` uses `full.relative_to(self.cwd)` (Python 3.9+, correct). Cognition replaces this with `str(full_path).startswith(str(self.cwd))` — the same structurally-buggy check tracked in P3-SEC-3. This regression exists in both `CognitionLocalSandboxBackend` and `CognitionDockerSandboxBackend`.

2. **Introduces an unnecessary `LocalSandbox` delegation layer.** `LocalSandbox` (`execution/sandbox.py`) exists purely to call `shlex.split()` + `shell=False` instead of `shell=True`. This is a legitimate security improvement — but it should be a two-line `execute()` override, not an entire indirection class.

**Fix:**
- Subclass `deepagents.backends.LocalShellBackend` directly
- Override only `execute()` to use `shlex.split()` + `subprocess.run(shell=False)` (preserving Cognition's security posture vs. `LocalShellBackend`'s `shell=True`)
- Delete the custom `_resolve_path()` overrides in both `CognitionLocalSandboxBackend` and `CognitionDockerSandboxBackend` — the parent class already has the correct implementation with `virtual_mode` support
- `LocalSandbox` (`execution/sandbox.py`) becomes deletable once the delegation is removed

**Result:** The local backend shrinks to ~20 lines (just the `execute()` override). `virtual_mode` works correctly. The `str.startswith()` path regression is eliminated for the local backend.

**Note on Docker backend:** `CognitionDockerSandboxBackend` should subclass `FilesystemBackend` (not `LocalShellBackend`) since it only needs file ops from the parent — execution goes through Docker. The `_resolve_path()` override there should also be removed in favour of calling `super().__init__(virtual_mode=True)`.

**Acceptance Criteria:**
- [ ] `CognitionLocalSandboxBackend` subclasses `LocalShellBackend`; no custom `_resolve_path()` override
- [ ] `execute()` override uses `shlex.split()` + `shell=False` (not `shell=True`)
- [ ] `CognitionDockerSandboxBackend` subclasses `FilesystemBackend` with `virtual_mode=True`; no custom `_resolve_path()` override
- [ ] `LocalSandbox` class in `execution/sandbox.py` removed (or reduced to a deprecated shim if tests depend on it)
- [ ] All existing sandbox tests pass unchanged
- [ ] Unit test: path `/../../etc/passwd` is rejected by `_resolve_path()` for both backends (verifying deepagents' correct check is now in effect)

---

### P3-ALN-2: Remove `ExecutionBackend` Protocol and `ExecutionBackendAdapter`

| Field | Value |
|-------|-------|
| **Layer** | 3 (Execution) |
| **File** | `server/app/execution/backend.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Dependencies** | P3-ALN-1 (removes `LocalExecutionBackend` usage) |

**Problem:** `server/app/execution/backend.py` defines a custom `ExecutionBackend` Protocol (lines 36–119) that is a hand-rolled redeclaration of `deepagents.backends.protocol.SandboxBackendProtocol`. It then defines `ExecutionBackendAdapter` (lines 257–341) which wraps an `ExecutionBackend` back into a `deepagents.SandboxBackendProtocol`-compatible interface — an adapter that converts from a protocol back to the same protocol it mirrors.

The adapter is not used in the live request path. `CognitionDockerSandboxBackend` calls `DockerExecutionBackend.execute()` directly. The `ExecutionBackend` Protocol and `ExecutionBackendAdapter` add ~200 lines of abstraction that deepagents already provides at the protocol level.

**What to keep:** `DockerExecutionBackend` — this is genuine Cognition-specific functionality that deepagents has no equivalent for. It should be retained and made to implement `deepagents.backends.protocol.SandboxBackendProtocol` directly (which it effectively already does via the adapter).

**Fix:**
- Delete `ExecutionBackend` Protocol (lines 36–119) — replace all uses with `deepagents.backends.protocol.SandboxBackendProtocol`
- Delete `ExecutionBackendAdapter` (lines 257–341) — nothing should be routing through it
- Delete `LocalExecutionBackend` (lines 120–255) — superseded by P3-ALN-1's `CognitionLocalSandboxBackend` refactor
- Retain `DockerExecutionBackend`; make it implement `SandboxBackendProtocol` directly
- Update any imports that reference the deleted classes

**Acceptance Criteria:**
- [ ] `ExecutionBackend` Protocol deleted; no references remain
- [ ] `ExecutionBackendAdapter` deleted; no references remain
- [ ] `LocalExecutionBackend` deleted; no references remain
- [ ] `DockerExecutionBackend` type-checks against `deepagents.backends.protocol.SandboxBackendProtocol`
- [ ] `mypy` passes with no new errors
- [ ] All existing backend tests pass (updated to remove tests for deleted classes)

---

### P3-ALN-3: Fix Broken `AgentMiddleware` Import and Tool Name Validation in `cli.py`

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) |
| **File** | `server/app/cli.py` |
| **Status** | **Complete** |
| **Effort** | ~30 minutes |

**Problem 1 — Broken import:** `server/app/cli.py:621` contains:

```python
from deepagents.middleware import AgentMiddleware
```

`deepagents.middleware` does **not** export `AgentMiddleware` in version 0.3.12 (or any known version). The package exports: `FilesystemMiddleware`, `MemoryMiddleware`, `SkillsMiddleware`, `SubAgent`, `SubAgentMiddleware`, `SummarizationMiddleware`. This import raises `ImportError` at runtime whenever a user runs `cognition create middleware <name>`, making the command non-functional.

The correct import — used correctly in `server/app/agent/middleware.py` and `server/app/agent_registry.py` — is:

```python
from langchain.agents.middleware.types import AgentMiddleware
```

**Problem 2 — Invalid tool name accepted silently:** `cognition create tool <name>` transforms the input with `.lower().replace("-", "_").replace(" ", "_")` but never validates that the result is a legal Python identifier. `cognition create tool 123-my-tool` produces a file containing `def 123_my_tool(...)` — a `SyntaxError` that will be silently swallowed at discovery time (caught as a file load error with no user-visible feedback).

**Fix:**
- One-line import correction in `cli.py:621`
- Add `str.isidentifier()` check after name transformation; exit with a clear error if the result is not a valid Python identifier
- Fix misleading "Next steps" text in `cognition create tool` output: remove step 3 ("Use AgentRegistry to register the tool for sessions") — auto-discovery makes this unnecessary and the instruction is incorrect

**Acceptance Criteria:**
- [ ] `cli.py` import corrected to `from langchain.agents.middleware.types import AgentMiddleware`
- [ ] `cognition create middleware test_mw` runs without `ImportError`
- [ ] Generated middleware template contains the correct import line
- [ ] `cognition create tool 123bad` exits with `Error: '123bad' is not a valid Python identifier` before creating any file
- [ ] `cognition create tool my-tool` (transforms to `my_tool`) works correctly — transform then validate
- [ ] "Next steps" output no longer includes the manual `AgentRegistry.register_tool()` instruction

---

## P3-SEC — Security Hardening ✅ 100% Complete

**Must be completed before P3-TR.** The `AgentRegistry` tool loading path (`_load_tools_from_file`) and the `AgentDefinition.tools` import path both execute arbitrary Python in the server process with zero guards. Connecting the registry to the live request path without these fixes would wire a code execution vulnerability directly into every API call.

Security fixes override all priorities per governance rules.

---

### P3-SEC-1: AST Import Scanning Before `exec_module`

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent_registry.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Severity** | CRITICAL |

**Problem:** `_load_tools_from_file()` calls `spec.loader.exec_module(module)` — equivalent to `exec(open(file).read())` — with no pre-execution validation. Any module-level code in a `.py` file dropped into `.cognition/tools/` runs immediately in the server process, which holds all LLM API keys and database credentials in its environment.

**Fix:** Before calling `exec_module`, parse the file with `ast.parse()` and walk the AST, rejecting (or warning loudly on) imports of dangerous stdlib modules: `os`, `subprocess`, `socket`, `ctypes`, `sys`, `shutil`, `importlib`, `pty`, `signal`, `multiprocessing`, `threading`, `concurrent`, `code`, `codeop`, `builtins`. Also flag direct use of `exec`, `eval`, `compile`, and `__import__` as call nodes.

**Behavior options (configurable via settings):**
- `tool_security = "warn"` — log a structured warning, continue loading (default for development)
- `tool_security = "strict"` — refuse to load the file, emit an error event (default for production)

**Acceptance Criteria:**
- [ ] `ast.parse()` called on file contents before `exec_module`
- [ ] Banned import/call detection implemented as an `ast.NodeVisitor`
- [ ] `tool_security` setting added to `Settings` with `"warn"` default
- [ ] In `strict` mode, file is skipped and error is logged; `discover_tools()` continues with remaining files
- [ ] Unit tests: clean file loads, banned import warns/blocks, nested import in function body detected
- [ ] No performance regression on clean files (AST parse of a 200-line file is <1ms)

---

### P3-SEC-2: Protect `.cognition/` from Agent Writes

| Field | Value |
|-------|-------|
| **Layer** | 3 (Execution) |
| **File** | `server/app/execution/backend.py` |
| **Status** | **Complete** |
| **Effort** | ~0.5 days |
| **Severity** | HIGH |

**Problem:** In `local` sandbox mode (the docker-compose default), the AI agent has `write_file` and `execute` access to the entire workspace, including `.cognition/tools/`. A compromised conversation could write a malicious `.py` file there and trigger a hot-reload to execute it in the server process. The path confinement check explicitly *allows* this because `.cognition/tools/` is inside `workspace_path`.

**Fix:** Add a protected-path deny list to `LocalExecutionBackend.write_file()` and `execute()`. Any path resolving under `{workspace_root}/.cognition/` requires explicit opt-in or is blocked entirely. The deny list should be configurable but default to blocking `.cognition/`.

**Acceptance Criteria:**
- [ ] `write_file()` raises `PermissionError` (mapped to a `CognitionError` subclass) for paths under `.cognition/`
- [ ] `execute()` blocks commands that would write to `.cognition/` (e.g. `>` redirection to those paths) — best-effort via path inspection; not a replacement for OS-level controls
- [ ] Protected paths are configurable via settings (`protected_paths: list[str]`)
- [ ] `DockerExecutionBackend` gets the same protection
- [ ] Unit tests: write to `.cognition/tools/evil.py` is blocked; write to `src/foo.py` is allowed

---

### P3-SEC-3: Fix Path Confinement Check (`str.startswith` → `Path.is_relative_to`)

| Field | Value |
|-------|-------|
| **Layer** | 3 (Execution) |
| **Files** | `server/app/execution/backend.py`, `server/app/agent/sandbox_backend.py` |
| **Status** | **Complete** |
| **Effort** | ~0.5 days |
| **Severity** | MEDIUM |

**Problem:** Path traversal confinement uses `str(full_path).startswith(str(self.root_dir))`. This is structurally incorrect: if `root_dir` is `/workspace` and a crafted path resolves to `/workspace-extra/etc/passwd`, the check passes as a false negative because `"/workspace-extra".startswith("/workspace")` is `True`.

**Fix:** Replace all `str.startswith(str(root))` path confinement checks with `Path.is_relative_to(root)` (Python 3.9+, already required by this project).

**Acceptance Criteria:**
- [ ] `backend.py:_resolve_path()` uses `full_path.is_relative_to(self.root_dir)`
- [ ] `sandbox_backend.py` equivalent check updated
- [ ] Unit test: path like `/workspace-extra/secret` is correctly rejected when root is `/workspace`

---

### P3-SEC-4: Harden `AgentDefinition.tools` Module Allowlist

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) / 4 (Agent Runtime) |
| **Files** | `server/app/agent/definition.py`, `server/app/agent/runtime.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Severity** | HIGH |

**Problem:** `AgentDefinition.tools` accepts any dotted string with at least one `.` as a valid tool path (`validate_tools` in `definition.py:119`). `runtime.py` resolves these via `__import__()`, which searches the entire `sys.path`. A YAML file with `tools: ["os.system"]` would give the LLM `os.system` as a callable tool.

**Fix:** Introduce a `trusted_tool_namespaces` setting (default: `["server.app.tools"]`). During `create_agent_runtime()` resolution, reject any `tool_path` whose module component does not start with a trusted namespace. This does not change the existing declarative YAML format — it adds a validation gate at import time.

**Acceptance Criteria:**
- [ ] `trusted_tool_namespaces: list[str]` setting added to `Settings` (default `["server.app.tools"]`)
- [ ] `create_agent_runtime()` validates each tool path against the allowlist before `__import__()`
- [ ] Paths outside the allowlist raise a `CognitionError` subclass (not silently skip)
- [ ] The allowlist is extensible so operators can add their own namespaces
- [ ] Unit tests: `os.system` rejected; `server.app.tools.my_tool` accepted; custom namespace accepted when configured

---

### P3-SEC-5: Tighten CORS Default from Wildcard

| Field | Value |
|-------|-------|
| **Layer** | 6 (API & Streaming) |
| **File** | `server/app/settings.py` |
| **Status** | **Complete** |
| **Effort** | ~0.5 days |
| **Severity** | MEDIUM |

**Problem:** `cors_origins`, `cors_methods`, and `cors_headers` all default to `["*"]` in `Settings`. This means any web page can make cross-origin requests to the Cognition API, enabling CSRF-style attacks.

**Fix:** Change defaults to safe values. `cors_origins` should default to `["http://localhost:3000", "http://localhost:8080"]` (common dev front-end ports) rather than `["*"]`. Document that production deployments must set `COGNITION_CORS_ORIGINS` explicitly. Add a startup warning log when `"*"` is detected.

**Acceptance Criteria:**
- [ ] `cors_origins` default changed from `["*"]` to localhost-only list
- [ ] Startup log emits a warning when `cors_origins` contains `"*"`
- [ ] `COGNITION_CORS_ORIGINS` env var documented in `config.example.yaml`
- [ ] Existing tests updated to explicitly set `cors_origins=["*"]` where needed

---

## P3-TR — Tool Registry End-to-End ✅ 100% Complete

**Blocked on P3-SEC.** The `AgentRegistry` infrastructure (P2-9), file watcher (P2-10), and CLI scaffolding (P2-11) are fully implemented but disconnected from the live request path. `DeepAgentStreamingService.stream_response()` calls `create_cognition_agent()` with no tools — the registry exists but is dead code. This tier wires everything together and adds the API surface to inspect registered tools.

**Partial implementation complete:**
- ✅ P3-TR-1: Fix tool discovery logic in `AgentRegistry`
- ✅ P3-TR-2: Wire `AgentRegistry` into `main.py` Lifespan + File Watcher
- ✅ P3-TR-3: Wire `AgentRegistry` into `stream_response()`
- ✅ P3-TR-4: `GET /tools` and `GET /tools/{name}` API Endpoints
- ✅ P3-TR-5: `ToolSecurityMiddleware` — Runtime Audit Log and Tool Blocklist
- ✅ P3-TR-6: Document and Wire Available Upstream Middleware (complete)
- ✅ P3-TR-7: Surface Tool Load Errors to Users (complete)
- ✅ P3-TR-8: `cognition tools list` and `cognition tools reload` CLI Commands (complete)
- ✅ P3-TR-9: Ensure `.cognition/` Directory Exists Before First Write

---

### P3-TR-1: Fix Tool Discovery Logic in `AgentRegistry`

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent_registry.py` |
| **Status** | **Complete** |
| **Effort** | ~0.5 days |
| **Dependencies** | P3-SEC-1 |

**Problem:** `_load_tools_from_file()` checks `hasattr(obj, "_tool_decorator")` (line 431) — this attribute does not exist on LangChain `@tool`-decorated objects. LangChain's `@tool` produces a `StructuredTool` instance (a `BaseTool` subclass). The second branch `isinstance(obj, BaseTool)` already catches these, making the first branch dead code that adds confusion. Additionally, the lambda closure `factory=lambda obj=obj: obj` returns the same singleton instance every call — fine for stateless tools, but should be documented clearly.

**Fix:**
- Remove the dead `_tool_decorator` branch entirely
- Detect tools via `isinstance(obj, BaseTool)` only (covers both `@tool`-decorated functions and explicit `BaseTool` subclasses)
- Add a module-level `__all__` check: if the tool file defines `__all__`, only inspect those names (respects author intent)
- Document the singleton factory behavior in docstrings

**Acceptance Criteria:**
- [ ] `_tool_decorator` check removed
- [ ] `isinstance(obj, BaseTool)` is the sole detection mechanism
- [ ] `__all__`-aware filtering implemented
- [ ] Existing `test_agent_registry.py` tests updated to use real `@tool` decorated functions
- [ ] New test: file that defines `__all__` only exposes listed tools

---

### P3-TR-2: Wire `AgentRegistry` into `main.py` Lifespan + File Watcher

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) / 4 (Agent Runtime) |
| **Files** | `server/app/main.py`, `server/app/file_watcher.py` |
| **Status** | **Complete** |
| **Effort** | ~0.5 days |
| **Dependencies** | P3-TR-1, P3-SEC-2 |

**Problem:** `initialize_agent_registry()` is never called in `main.py`. The `AgentRegistry` singleton is never set, so `get_agent_registry()` raises `RuntimeError` at every request. The `WorkspaceWatcher` is also never started, so hot-reload is inoperative. Two additional bugs in `file_watcher.py` mean hot-reload would be broken even after wiring:

1. **`asyncio.get_event_loop()` in watchdog's OS thread** (`_schedule_change`, line ~382): watchdog fires events from a background OS thread, not from the asyncio event loop. In Python 3.10+ `get_event_loop()` raises `DeprecationWarning` or `RuntimeError` in a thread with no running loop. The fix is to capture `asyncio.get_event_loop()` once at `start()` time (on the main thread) and use `loop.call_soon_threadsafe(...)` / `asyncio.run_coroutine_threadsafe(coro, loop)` in the callback.

2. **`mark_middleware_pending()` does not exist on `AgentRegistry`** (`_process_change`, line ~439): any file change under `.cognition/middleware/` triggers `self.agent_registry.mark_middleware_pending()`, which raises `AttributeError`. The exception is swallowed by the outer `except`, logged as `"Failed to process file change"`, and middleware hot-reload silently fails every time.

**Fix:** Add to `lifespan()` in `main.py`:
1. Call `initialize_agent_registry(session_manager, settings)` after `initialize_session_manager()`
2. Create and start a `WorkspaceWatcher` pointing at `.cognition/tools/` and `.cognition/middleware/`
3. Stop the watcher in shutdown cleanup

Fix `file_watcher.py`:
- Capture the running event loop at `start()` time; use `loop.call_soon_threadsafe` in `_schedule_change`
- Replace `self.agent_registry.mark_middleware_pending()` with the correct method call (or implement the method on `AgentRegistry` if session-based middleware pending state is desired)

The watcher should only watch directories that actually exist — graceful no-op if `.cognition/tools/` is absent.

**Acceptance Criteria:**
- [ ] `get_agent_registry()` no longer raises `RuntimeError` on a running server
- [ ] `WorkspaceWatcher` started at server boot, stopped at shutdown
- [ ] File drop into `.cognition/tools/` triggers `reload_tools()` within debounce window on Python 3.11/3.12
- [ ] File change in `.cognition/middleware/` does not raise `AttributeError`
- [ ] `_schedule_change` uses `loop.call_soon_threadsafe` — no `asyncio.get_event_loop()` in watchdog thread
- [ ] Server starts cleanly when `.cognition/tools/` does not exist
- [ ] Unit test: lifespan initializes registry and watcher
- [ ] Unit test: `_schedule_change` called from a non-asyncio thread does not raise

---

### P3-TR-3: Wire `AgentRegistry` into `DeepAgentStreamingService.stream_response()`

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) / 5 (LLM Provider) |
| **File** | `server/app/llm/deep_agent_service.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Dependencies** | P3-TR-2 |

**Problem:** `stream_response()` (line 97–103) calls `create_cognition_agent()` with no `tools` argument. Tools discovered from `.cognition/tools/` are never passed to the agent. This is the core wiring gap.

**Fix:** Before calling `create_cognition_agent()`, retrieve the registry and materialise its tools:

```python
from server.app.agent_registry import get_agent_registry

try:
    registry = get_agent_registry()
    custom_tools = registry.create_tools()
except RuntimeError:
    custom_tools = []  # Registry not initialized (test/dev contexts)

agent = create_cognition_agent(
    project_path=project_path,
    model=model,
    store=None,
    checkpointer=checkpointer,
    settings=llm_settings,
    tools=custom_tools if custom_tools else None,
)
```

Tools are global to all sessions (scoping is not per-agent at this tier). Agent cache key already includes `len(tools)`, so adding/removing tools correctly busts the cache.

**Acceptance Criteria:**
- [ ] `create_cognition_agent()` receives tools from the registry on every `stream_response()` call
- [ ] Empty registry (`custom_tools = []`) passes `tools=None` — no behavior change for zero-tool deployments
- [ ] `RuntimeError` from uninitialised registry is caught and degrades gracefully
- [ ] Unit test: mock registry with 2 tools → verify `create_cognition_agent` called with those tools
- [ ] E2E scenario: drop a `@tool`-decorated file in `.cognition/tools/` → start session → agent uses the tool

---

### P3-TR-4: `GET /tools` and `GET /tools/{name}` API Endpoints

| Field | Value |
|-------|-------|
| **Layer** | 6 (API & Streaming) |
| **Files** | `server/app/api/routes/tools.py` (new), `server/app/api/models.py`, `server/app/main.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Dependencies** | P3-TR-2 |

Expose the tool registry via the API, following the same pattern as `GET /agents`.

**New endpoints:**
```
GET  /tools           — list all registered tools
GET  /tools/{name}    — get single tool by name (404 if not found)
```

**Response models:**
```python
class ToolResponse(BaseModel):
    name: str
    source: str        # "programmatic" or absolute file path
    module: str | None # dotted module name if loaded from file

class ToolList(BaseModel):
    tools: list[ToolResponse]
    count: int
```

**Acceptance Criteria:**
- [ ] `GET /tools` returns all registered tools with correct fields
- [ ] `GET /tools/{name}` returns 404 when tool not found
- [ ] Router wired into `main.py` via `app.include_router(tools.router)`
- [ ] Response model added to `api/models.py`
- [ ] Unit tests for both endpoints (empty registry, populated registry, 404)

---

### P3-TR-5: `ToolSecurityMiddleware` — Runtime Audit Log and Tool Blocklist

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent/middleware.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Dependencies** | P3-TR-3 |

**Context:** P3-SEC-1 (AST scanning) is a load-time gate — it inspects Python source before `exec_module()` runs. It cannot prevent a tool that passed scanning from being called at runtime, and it produces no per-invocation visibility. `ToolSecurityMiddleware` is the runtime complement: it intercepts every tool call via `awrap_tool_call`, logs it to the structured log pipeline, and enforces a configurable blocklist as defence-in-depth.

deepagents and langchain have no equivalent — the existing `HumanInTheLoopMiddleware` provides human approval flows, `ToolCallLimitMiddleware` enforces call counts, but neither provides audit logging or a server-side name-based blocklist tied to the session context.

**Implementation:** Subclass `AgentMiddleware`, implement `awrap_tool_call`:

```python
class ToolSecurityMiddleware(AgentMiddleware):
    def __init__(self, blocked_tools: list[str] | None = None) -> None:
        self.blocked_tools: set[str] = set(blocked_tools or [])

    async def awrap_tool_call(self, request, handler):
        tool_name = request.tool.name if request.tool else request.tool_call["name"]
        session_id = getattr(request.runtime.config, "configurable", {}).get("thread_id")

        logger.info("tool_call", tool=tool_name, session_id=session_id,
                    args=request.tool_call.get("args"))

        if tool_name in self.blocked_tools:
            logger.warning("tool_blocked", tool=tool_name, session_id=session_id)
            return ToolMessage(
                content=f"Tool '{tool_name}' is disabled by server policy.",
                tool_call_id=request.tool_call["id"],
                name=tool_name,
                status="error",
            )

        return await handler(request)
```

The `blocked_tools` list is sourced from `Settings.blocked_tools: list[str]` (default `[]`), so operators can disable specific tools via environment variable without code changes.

`ToolSecurityMiddleware` is instantiated in `create_cognition_agent()` alongside `CognitionObservabilityMiddleware` and `CognitionStreamingMiddleware` — it is always active, not opt-in.

**Acceptance Criteria:**
- [ ] `ToolSecurityMiddleware` implemented in `server/app/agent/middleware.py`
- [ ] `Settings.blocked_tools: list[str]` added with default `[]`
- [ ] Every tool call produces a `structlog` `INFO` event with `tool`, `session_id`, `args`
- [ ] Tools in `blocked_tools` return `ToolMessage(status="error")` without calling handler
- [ ] Middleware passed to `create_cognition_agent()` unconditionally
- [ ] Unit tests: allowed tool calls pass through, blocked tool returns error message, args are logged
- [ ] Blocked tool name is case-sensitive (exact match)

---

### P3-TR-6: Document and Wire Available Upstream Middleware

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) / 1 (Foundation) |
| **Files** | `server/app/agent/definition.py`, `server/app/agent/runtime.py`, `config.example.yaml` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Dependencies** | P3-TR-3 |

**Context:** The installed `langchain` package ships four production-quality middleware implementations that are directly relevant to Cognition's use cases and have no equivalent in the current codebase:

| Middleware | Import | Purpose |
|---|---|---|
| `ToolRetryMiddleware` | `langchain.agents.middleware` | Exponential backoff retry on tool failure, per-tool filtering |
| `ToolCallLimitMiddleware` | `langchain.agents.middleware` | Per-tool and global call limits, thread-level and run-level |
| `HumanInTheLoopMiddleware` | `langchain.agents.middleware` | Approve/edit/reject tool calls before execution (uses LangGraph `interrupt()`) |
| `PIIMiddleware` | `langchain.agents.middleware` | Detect and redact PII in messages (email, credit card, IP, custom regex) |

None of these are currently reachable by Cognition users — there is no mechanism to enable them via `AgentDefinition` YAML or `config.yaml` without writing Python. A user who wants retry logic on their custom tools cannot express that today.

**Fix:** Extend `AgentDefinition.middleware` to accept a list of declarative middleware specs alongside raw Python imports. Add a resolver in `create_agent_runtime()` that maps well-known names to their upstream constructors with configurable parameters:

```yaml
# .cognition/agents/my-agent.yaml
middleware:
  - name: tool_retry
    max_retries: 3
    backoff_factor: 2.0
  - name: tool_call_limit
    run_limit: 20
    exit_behavior: continue
  - name: pii
    pii_type: email
    strategy: redact
```

The resolver maps `tool_retry` → `ToolRetryMiddleware(...)`, `tool_call_limit` → `ToolCallLimitMiddleware(...)`, `pii` → `PIIMiddleware(...)`, `human_in_the_loop` → `HumanInTheLoopMiddleware(...)`. Unrecognised names fall back to the existing dotted-import-path resolution.

Also add `config.example.yaml` entries documenting these options, and a note in `AGENTS.md` listing them.

**What this does NOT do:** Does not add new Python — these are wrappers around already-installed code. Does not expose `HumanInTheLoopMiddleware` as a server-side automatic approval mechanism (that requires client-side interrupt handling, which is a separate P4 concern).

**Acceptance Criteria:**
- [ ] `AgentDefinition.middleware` supports both dotted import strings and `{name: ..., **kwargs}` dicts
- [ ] Resolver in `create_agent_runtime()` maps `tool_retry`, `tool_call_limit`, `pii`, `human_in_the_loop` to their langchain constructors
- [ ] Unknown names continue to resolve via dotted import path (no regression)
- [ ] `config.example.yaml` documents all four middleware options with example parameters
- [ ] Unit tests: YAML spec resolves to correct middleware class with correct parameters
- [ ] `mypy` passes — resolver is typed correctly

---

### P3-TR-7: Surface Tool Load Errors to Users

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) / 6 (API & Streaming) |
| **Files** | `server/app/agent_registry.py`, `server/app/llm/deep_agent_service.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Dependencies** | P3-TR-2 |

**Problem:** When a `.cognition/tools/*.py` file fails to load — due to a syntax error, a bad import, a missing dependency, or a failed factory instantiation — the only output is a server-side structlog `ERROR`. The user sees nothing: `tools_discovered` logs `0`, the agent runs without the tool, and there is no indication of what went wrong. On hot-reload triggered by saving a file, the failure is entirely invisible.

This is the single most likely source of "my tool isn't working" confusion in the tool registry UX.

**Fix — two surfaces:**

1. **`_load_tools_from_file()` error accumulation:** Instead of only logging and returning `0`, accumulate errors into a list on the registry instance (`AgentRegistry._load_errors: list[ToolLoadError]`). Each error records `file`, `error_type`, `message`, and `timestamp`.

2. **SSE event on hot-reload failure:** When `reload_tools()` is triggered by the file watcher and a file fails to load, emit a `tool_load_error` SSE event to active sessions via the existing streaming middleware. Format:
   ```json
   {"type": "tool_load_error", "file": "my_tool.py", "error": "SyntaxError: invalid syntax (line 12)"}
   ```

3. **`GET /tools/errors` endpoint:** Returns the accumulated load error list. Cleared on successful reload of the affected file.

**Acceptance Criteria:**
- [ ] `AgentRegistry._load_errors` accumulates `ToolLoadError` entries for each failed file
- [ ] Errors are cleared per-file on successful reload (not globally)
- [ ] `GET /tools/errors` endpoint returns current error list (empty list when no errors)
- [ ] `reload_tools()` emits a `tool_load_error` SSE event for each failed file
- [ ] `spec_from_file_location` returning `None` is no longer a silent failure — logs `WARNING` with file path
- [ ] Unit tests: syntax error in tool file → error recorded in registry → cleared on fix

---

### P3-TR-8: `cognition tools list` and `cognition tools reload` CLI Commands

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) / 6 (API & Streaming) |
| **File** | `server/app/cli.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Dependencies** | P3-TR-4 (requires `GET /tools` endpoint) |

**Problem:** There is no CLI surface to inspect or manually trigger the tool registry. A user who drops a file into `.cognition/tools/` and wants to confirm it was loaded has to read server logs and search for `"Tool discovery complete"`. There is no way to force a reload without restarting the server.

**Fix:** Add a `cognition tools` command group with two subcommands:

```
cognition tools list
  — Calls GET /tools, renders a Rich table: name | source file | status
  — Also calls GET /tools/errors and renders any load errors below the table
  — Exit code 1 if any load errors are present (useful for CI)

cognition tools reload
  — Calls POST /tools/reload (new endpoint, triggers reload_tools() server-side)
  — Prints count of tools loaded, lists any errors
  — Exit code 1 if reload produced errors
```

**New API endpoint required:**
```
POST /tools/reload   — triggers AgentRegistry.reload_tools(), returns {count, errors}
```

**Acceptance Criteria:**
- [ ] `cognition tools list` renders a Rich table of registered tools
- [ ] `cognition tools list` renders load errors below the table if any
- [ ] `cognition tools list` exits with code 1 when errors are present
- [ ] `cognition tools reload` triggers server-side reload and reports result
- [ ] `POST /tools/reload` endpoint implemented and wired into `main.py`
- [ ] Both commands handle server-not-running gracefully (clear error, not a traceback)

---

### P3-TR-9: Ensure `.cognition/` Directory Exists Before First Write

| Field | Value |
|-------|-------|
| **Layer** | 6 (API & Streaming) / 1 (Foundation) |
| **Files** | `server/app/api/routes/config.py`, `server/app/agent_registry.py` |
| **Status** | **Complete** |
| **Effort** | ~0.5 days |
| **Dependencies** | None |

**Problem:** Two code paths write to `.cognition/` without ensuring the directory exists first:

1. `config.py` route writes to `.cognition/config.yaml` directly — raises `FileNotFoundError` if `.cognition/` doesn't exist (i.e. user has never run `cognition create tool` or `cognition init`).
2. `initialize_agent_registry()` reads `settings.workspace_path / ".cognition" / "tools"` but does not create it — `discover_tools()` silently returns `0` at `DEBUG` level if absent.

**Fix:**
- `config.py` write path: add `path.parent.mkdir(parents=True, exist_ok=True)` before open
- `initialize_agent_registry()`: create `.cognition/tools/` and `.cognition/middleware/` at startup with `mkdir(parents=True, exist_ok=True)` — this makes the directory structure self-healing and removes the "directory doesn't exist" edge case from the watcher

**Acceptance Criteria:**
- [ ] `PATCH /config` succeeds on a fresh workspace where `.cognition/` does not yet exist
- [ ] Server startup creates `.cognition/tools/` and `.cognition/middleware/` if absent
- [ ] `mkdir` calls use `exist_ok=True` — no error if directories already exist
- [ ] Unit test: config write succeeds when parent directory is absent

---

## P4 — Extended Vision (0% Complete)

Deferred from P3. Unblocked after P3 is complete.

### P4-1: Cloud Execution Backends

| Field | Value |
|-------|-------|
| **Layer** | 3 (Execution) |
| **Status** | **Complete** |
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
| **Status** | **Complete** |
| **Effort** | ~1-2 weeks |
| **Dependencies** | P2-2 (Circuit Breaker) ✅ |

**Acceptance Criteria:**
- [ ] Ollama provider factory registered
- [ ] Streaming-level fallback: mid-stream provider failure triggers fallback
- [ ] Provider factories return typed protocol, not `Any`
- [ ] Gateway integration option (MLflow AI Gateway or LiteLLM)

### P4-3: Remote MCP (Model Context Protocol) Support ✅

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **Status** | **Complete** |
| **Effort** | ~1 week |
| **Dependencies** | P3-TR (Tool Registry) ✅ |

**Security-First Design:**
Cognition implements **remote-only MCP** for security. Local (stdio) MCP servers are explicitly rejected.

| Capability | Native Tools | MCP Tools |
|------------|--------------|-----------|
| Local execution (shell, files) | ✅ Built-in tools | ❌ Not supported |
| Remote information (GitHub, Jira) | ❌ Limited | ✅ MCP servers |

**Implementation:**
- `server/app/agent/mcp_client.py` — Remote SSE client with URL validation
- `server/app/agent/mcp_adapter.py` — LangChain tool adapter  
- `docs/mcp.md` — User documentation with security stance
- HTTP/HTTPS only — local (stdio) MCP servers rejected with clear error
- Configurable via `mcp_servers` in settings or session creation

**Acceptance Criteria:**
- [x] `McpSseClient` connects to remote MCP servers via HTTP/SSE
- [x] URL validation rejects non-HTTP URLs (file://, stdio commands)
- [x] `McpAdapterTool` converts MCP tools to LangChain `BaseTool`
- [x] `create_cognition_agent()` accepts `mcp_configs` parameter
- [x] MCP tools integrated into agent alongside built-in tools
- [x] Graceful degradation when MCP connection fails
- [x] Documentation in `docs/mcp.md` explains security stance
- [x] 12 E2E tests covering security, connection, integration, errors

  - Color-coded output with pass/fail reporting
  - CI-friendly with non-zero exit on failures

### Known Issues

| Issue | Severity | Status |
|-------|----------|--------|
| MLflow `autolog()` ContextVar error in async contexts | Medium | Upstream MLflow bug. `run_tracer_inline=True` applied but insufficient. OTel tracing works as alternative. |
| Prompt registry not wired into agent factory | Low | Implementation exists, integration pending (GUITool was removed) |
| Evaluation feedback stored in-memory only | Low | Needs persistence backend (P3-1) |
| `AgentRegistry` never initialised in `main.py` lifespan | High | Registry infrastructure complete (P2-9) but `get_agent_registry()` raises `RuntimeError` at runtime. Tracked as P3-TR-2. |
| `AgentRegistry` not wired into `stream_response()` | High | Tools discovered from `.cognition/tools/` are never passed to the agent. Tracked as P3-TR-3. |
| `_load_tools_from_file()` dead `_tool_decorator` check | Medium | Detects `BaseTool` instances correctly via second branch, but first branch is dead code that adds confusion. Tracked as P3-TR-1. |
| Tool loading executes arbitrary Python with no guards | Critical | `exec_module()` runs module-level code before any inspection. No AST scanning, no import filtering. Tracked as P3-SEC-1. |
| Agent can write to `.cognition/tools/` and trigger reload | High | Path confinement allows writes inside workspace root, including `.cognition/`. Tracked as P3-SEC-2. |
| Path confinement uses `str.startswith` (false negatives) | Medium | `/workspace-extra` passes check when root is `/workspace`. Affects `CognitionLocalSandboxBackend` and `CognitionDockerSandboxBackend`. Tracked as P3-SEC-3 and P3-ALN-1. |
| `AgentDefinition.tools` accepts any dotted path incl. `os.system` | High | Validation only checks for presence of `.`. `runtime.py` will `__import__` anything. Tracked as P3-SEC-4. |
| CORS defaults to `["*"]` | Medium | All origins allowed by default. Tracked as P3-SEC-5. |
| `CognitionLocalSandboxBackend` reimplements `LocalShellBackend` with regressions | Medium | Removes `virtual_mode` support, introduces buggy `str.startswith` path check. Tracked as P3-ALN-1. |
| `ExecutionBackend` Protocol duplicates `deepagents.SandboxBackendProtocol` | Medium | ~200 lines of adapter indirection unused in live path. Tracked as P3-ALN-2. |
| `cognition create middleware` crashes with `ImportError` | High | `cli.py:621` imports `AgentMiddleware` from `deepagents.middleware` which does not export it. Tracked as P3-ALN-3. |
| Hot-reload never fires on Python 3.11/3.12 | High | `file_watcher._schedule_change` calls `asyncio.get_event_loop()` from watchdog's OS thread — raises `RuntimeError` in Python 3.10+. Tracked as P3-TR-2. |
| Middleware hot-reload always fails with `AttributeError` | High | `file_watcher._process_change` calls `agent_registry.mark_middleware_pending()` which does not exist on `AgentRegistry`. Caught silently. Tracked as P3-TR-2. |
| Tool load errors invisible to users | Medium | Syntax errors, import errors, and missing dependencies in `.cognition/tools/*.py` produce only a server-side structlog `ERROR`. No SSE event, no API response, no CLI feedback. Tracked as P3-TR-7. |
| `cognition create tool` accepts invalid Python identifiers | Medium | Name is transformed but not validated — `123bad` passes through, producing a `SyntaxError` at discovery time that is silently swallowed. Tracked as P3-ALN-3. |
| `cognition create tool` next-steps are misleading | Low | Step 3 instructs users to manually call `AgentRegistry.register_tool()` — auto-discovery makes this unnecessary. Tracked as P3-ALN-3. |
| No `cognition tools list` or `cognition tools reload` commands | Low | No CLI surface to inspect or manually trigger the tool registry. Tracked as P3-TR-8. |
| `.cognition/` not created by `config` route | Low | `config.py` route writes to `.cognition/config.yaml` without creating the parent directory — raises `FileNotFoundError` if the user has not yet run `cognition create tool`. Tracked as P3-TR-9. |
| `tools=[]` and `tools=None` share the same agent cache key | Low | Both map to `"0"` in the MD5 cache key. An empty-registry reload that later succeeds won't bust the cache until at least one tool is present. Tracked as P3-TR-3. |
| File-based tool discovery is untested | Medium | No test writes a `@tool`-decorated `.py` file to disk and calls `discover_tools()`. Detection logic could regress invisibly. Tracked as P3-TR-1. |

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

**P3 Complete!** Multi-Agent Registry fully implemented with built-in agents, user-defined agents, session-agent binding, and API endpoints.

### Recently Completed
- ✅ P3 Multi-Agent Registry: AgentDefinitionRegistry with built-in `default` and `readonly` agents
- ✅ P3 Markdown + YAML agent file loading with Deep Agents SubAgent TypedDict
- ✅ P3 Session–agent binding with Alembic migration for SQLite and Postgres
- ✅ P3 `GET /agents` and `GET /agents/{name}` endpoints with session creation validation
- ✅ 45 new unit tests for agent registry and 29 E2E scenario tests
- ✅ Subagent-mode validation prevents subagents from being used as session primaries

### Immediate — P3-ALN (Deep Agents Alignment, architecture correction — overrides feature work):
1. P3-ALN-1: Replace `CognitionLocalSandboxBackend` with thin subclass of `LocalShellBackend` (~1 day)
2. P3-ALN-2: Remove `ExecutionBackend` Protocol and `ExecutionBackendAdapter` (~1 day, depends on P3-ALN-1)
3. P3-ALN-3: Fix broken `AgentMiddleware` import in `cli.py` (~15 min, independent)

### Next — P3-SEC (Security Hardening, blocks P3-TR):
1. P3-SEC-1: AST import scanning before `exec_module` in `AgentRegistry` (~1 day) — CRITICAL
2. P3-SEC-2: Protect `.cognition/` from agent writes in `LocalExecutionBackend` (~0.5 days) — HIGH
3. P3-SEC-3: Fix `str.startswith` → `Path.is_relative_to` in path confinement (~0.5 days) — MEDIUM (partially resolved by P3-ALN-1)
4. P3-SEC-4: `AgentDefinition.tools` module namespace allowlist (~1 day) — HIGH
5. P3-SEC-5: Tighten CORS default from `["*"]` (~0.5 days) — MEDIUM

### Then — P3-TR (Tool Registry End-to-End, unblocked after P3-SEC):
1. P3-TR-1: Fix tool discovery logic + add real file-based tests (~0.5 days)
2. P3-TR-2: Wire `AgentRegistry` into `main.py` lifespan + fix `asyncio` thread bug + fix `mark_middleware_pending()` (~1 day)
3. P3-TR-3: Wire `AgentRegistry` into `stream_response()` + fix `tools=[]`/`tools=None` cache key (~1 day)
4. P3-TR-4: `GET /tools` and `GET /tools/{name}` API endpoints (~1 day)
5. P3-TR-5: `ToolSecurityMiddleware` — runtime audit log + tool blocklist via `awrap_tool_call` (~1 day)
6. P3-TR-6: Wire available upstream middleware (`ToolRetryMiddleware`, `ToolCallLimitMiddleware`, `HumanInTheLoopMiddleware`, `PIIMiddleware`) into `AgentDefinition` YAML (~1 day)
7. P3-TR-7: Surface tool load errors via `GET /tools/errors` + SSE event (~1 day)
8. P3-TR-8: `cognition tools list` and `cognition tools reload` CLI commands + `POST /tools/reload` (~1 day)
9. P3-TR-9: Ensure `.cognition/` directory exists before first write (~0.5 days)

### Deferred — P4 Extended Vision:
1. P4-1: Cloud Execution Backends (ECS/Lambda) (~2–4 weeks)
2. P4-2: Ollama Provider + LLM Resilience (~1–2 weeks)

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

Automatically providing: REST API, SSE streaming, persistence, sandbox isolation, observability, multi-user scoping, and evaluation pipeline.# Cognition Roadmap

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
| **P3** (Multi-Agent Registry) | 4 | 4/4 | **100% ✅** |
| **P3-ALN** (Deep Agents Alignment) | 3 | 3/3 | **100% ✅** |
| **P3-SEC** (Security Hardening) | 5 | 5/5 | **100% ✅** |
| **P3-TR** (Tool Registry End-to-End) | 9 | 9/9 | **100% ✅** |
| **P4** (Extended Vision) | 2 | 0/2 | **0% — Deferred** |

**Unit tests:** 338 passed, 4 skipped, 2 warnings
**E2E Business Scenarios:** 29/29 scenarios passing across P2 Cleanup, Robustness, GUI Extensibility, and P3 Multi-Agent Registry
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

## P2 — Robustness + GUI Extensibility ✅ 100% Complete

This priority tier combines robustness improvements, technical debt cleanup, and new extensibility APIs for GUI applications. A key theme is **aligning with LangGraph/Deep Agents primitives** — removing custom code that duplicates what the framework already provides, and building only what LangGraph cannot.

All P2 items complete: Cleanup (5/5), Robustness (7/7), GUI Extensibility (4/4).

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

## P3 — Multi-Agent Registry ✅ 100% Complete

Multi-agent registry system with built-in and user-defined agents, session-agent binding, and Deep Agents native subagent support.

**Design principle:** Leverage Deep Agents' native `SubAgent` TypedDict and `create_deep_agent(subagents=[...])` as the compilation primitive. Cognition owns only the registry, YAML/Markdown loading, translation layer, storage, and API surface. No custom routing logic.

**Deep Agents integration model:**
- Primary agents → compiled via `create_deep_agent(system_prompt=..., subagents=[...])`
- Subagent-mode agents → translated to `SubAgent` TypedDicts, passed into the primary agent's `create_deep_agent()` call; routing handled automatically by Deep Agents' injected `task` tool
- Per-agent model, temperature, skills overrides → mapped directly to `SubAgent.model`, `SubAgent.skills`, etc.

---

### P3-1: `AgentDefinitionRegistry` + Built-in Agents ✅

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent/agent_definition_registry.py` |
| **Status** | **Complete** |
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
    def is_valid_primary(self, name: str) -> bool: ...  # validates for session creation
```

**Global singleton helpers:** `get_agent_definition_registry()`, `initialize_agent_definition_registry(workspace_path, settings)`.

**Acceptance Criteria:**
- [x] Built-in `default` and `readonly` agents present in every registry instance
- [x] `initialize_agent_definition_registry(workspace_path, settings)` scans `.cognition/agents/` and merges user-defined agents
- [x] User agents override built-in agents when names collide (user wins)
- [x] `reload()` rescans without server restart
- [x] `is_valid_primary()` prevents subagent-mode agents from being used as session primaries
- [x] Unit tests covering: list, get, reload, built-ins present, user override, primary validation

---

### P3-2: Markdown + YAML Agent File Loading ✅

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent/definition.py` |
| **Status** | **Complete** |
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

**Markdown format** (`.cognition/agents/researcher.md`):
```markdown
---
name: researcher
description: A specialized research agent for gathering information
mode: subagent
---

You are a specialized research agent focused on gathering and synthesizing information.
You should search for relevant information, analyze sources, and provide comprehensive summaries.
```
- Filename stem becomes `name` (e.g., `researcher.md` → `name="researcher"`)
- YAML frontmatter maps to `AgentDefinition` fields
- Body becomes `system_prompt`

**YAML format** (`.cognition/agents/security-auditor.yaml`) — already supported by `load_agent_definition()`; now includes the new fields.

**New loader function:** `load_agent_definition_from_markdown(path) -> AgentDefinition`

**Translation to Deep Agents `SubAgent` TypedDict:**
```python
def to_subagent(self) -> SubAgent:
    """Translate to Deep Agents SubAgent TypedDict."""
    spec: SubAgent = {
        "name": self.name,
        "description": self.description or "",
        "system_prompt": self.system_prompt,
    }
    if self.config.model:
        spec["model"] = self.config.model
    if self.skills:
        spec["skills"] = self.skills
    return spec
```

**Acceptance Criteria:**
- [x] `load_agent_definition_from_markdown(path)` parses frontmatter + body correctly
- [x] `AgentDefinition.to_subagent()` returns a valid `SubAgent` TypedDict
- [x] `mode`, `description`, `hidden` fields validated by Pydantic
- [x] `native` field is read-only (set by registry code only)
- [x] Unit tests: markdown roundtrip, YAML roundtrip, `to_subagent()` translation, validation errors

---

### P3-3: Session–Agent Binding + Storage ✅

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) + 2 (Persistence) + 4 (Agent Runtime) |
| **Files** | `server/app/models.py`, `server/app/storage/schema.py`, `server/app/storage/sqlite.py`, `server/app/storage/postgres.py`, `server/app/llm/deep_agent_service.py` |
| **Status** | **Complete** |
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

**Storage schema change:** `agent_name TEXT NOT NULL DEFAULT 'default'` added to the sessions table. `to_dict()` / `from_dict()` updated. Alembic migration added (`003_add_agent_name.py`).

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
- [x] `Session.agent_name` persisted and loaded correctly from SQLite and Postgres
- [x] Sessions with unknown `agent_name` fall back to `"default"` on load (resilience)
- [x] Each session's primary agent is compiled with all `subagent`/`all`-mode agents from the registry available as subagents
- [x] Agent cache key includes `agent_name` so different agents don't share compiled instances
- [x] Alembic migration works for both SQLite and PostgreSQL
- [x] Unit tests: session persistence roundtrip, compilation path, cache keying

---

### P3-4: `GET /agents` API Endpoint ✅

| Field | Value |
|-------|-------|
| **Layer** | 6 (API & Streaming) |
| **Files** | `server/app/api/routes/agents.py`, `server/app/api/models.py`, `server/app/api/routes/__init__.py`, `server/app/api/routes/sessions.py` |
| **Status** | **Complete** |
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

Session creation validates that `agent_name` resolves to a known, non-hidden, `primary`/`all`-mode agent using `is_valid_primary()`. Returns `422` with a clear error if not.

**Scope:** Hidden agents (`hidden=True`) are excluded from `GET /agents` entirely. They remain accessible by exact name for internal use (e.g., by other agents via the `task` tool).

**Acceptance Criteria:**
- [x] `GET /agents` returns all non-hidden agents with correct fields
- [x] `GET /agents/{name}` returns 404 for unknown or hidden agents
- [x] `POST /sessions` with valid `agent_name` creates session bound to that agent
- [x] `POST /sessions` with invalid/hidden `agent_name` returns 422
- [x] `POST /sessions` with subagent-mode `agent_name` returns 422
- [x] `POST /sessions` without `agent_name` defaults to `"default"`
- [x] `GET /sessions/{id}` response includes `agent_name`
- [x] Unit tests for all endpoint cases; integration test for full session → agent compilation path

---

## P3-ALN — Deep Agents Alignment ✅ 100% Complete

**Architecture corrections override feature work** (AGENTS.md governance rule). These items were identified by auditing the actual `deepagents==0.3.12` package source against Cognition's current implementation. Each item removes duplication or corrects a regression introduced by reimplementing something the library already provides correctly.

**All three items complete.**

---

### P3-ALN-1: Replace `CognitionLocalSandboxBackend` with a thin subclass of `LocalShellBackend`

| Field | Value |
|-------|-------|
| **Layer** | 3 (Execution) |
| **Files** | `server/app/agent/sandbox_backend.py`, `server/app/execution/sandbox.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |

**Problem:** `CognitionLocalSandboxBackend` (`sandbox_backend.py:27-87`) is a near-complete reimplementation of `deepagents.backends.LocalShellBackend`. Both subclass `FilesystemBackend`, both implement `SandboxBackendProtocol`, both truncate at 100KB. However, the Cognition version introduces two regressions versus the upstream:

1. **Overrides `_resolve_path()` and removes `virtual_mode` support.** deepagents' `FilesystemBackend._resolve_path()` uses `full.relative_to(self.cwd)` (Python 3.9+, correct). Cognition replaces this with `str(full_path).startswith(str(self.cwd))` — the same structurally-buggy check tracked in P3-SEC-3. This regression exists in both `CognitionLocalSandboxBackend` and `CognitionDockerSandboxBackend`.

2. **Introduces an unnecessary `LocalSandbox` delegation layer.** `LocalSandbox` (`execution/sandbox.py`) exists purely to call `shlex.split()` + `shell=False` instead of `shell=True`. This is a legitimate security improvement — but it should be a two-line `execute()` override, not an entire indirection class.

**Fix:**
- Subclass `deepagents.backends.LocalShellBackend` directly
- Override only `execute()` to use `shlex.split()` + `subprocess.run(shell=False)` (preserving Cognition's security posture vs. `LocalShellBackend`'s `shell=True`)
- Delete the custom `_resolve_path()` overrides in both `CognitionLocalSandboxBackend` and `CognitionDockerSandboxBackend` — the parent class already has the correct implementation with `virtual_mode` support
- `LocalSandbox` (`execution/sandbox.py`) becomes deletable once the delegation is removed

**Result:** The local backend shrinks to ~20 lines (just the `execute()` override). `virtual_mode` works correctly. The `str.startswith()` path regression is eliminated for the local backend.

**Note on Docker backend:** `CognitionDockerSandboxBackend` should subclass `FilesystemBackend` (not `LocalShellBackend`) since it only needs file ops from the parent — execution goes through Docker. The `_resolve_path()` override there should also be removed in favour of calling `super().__init__(virtual_mode=True)`.

**Acceptance Criteria:**
- [ ] `CognitionLocalSandboxBackend` subclasses `LocalShellBackend`; no custom `_resolve_path()` override
- [ ] `execute()` override uses `shlex.split()` + `shell=False` (not `shell=True`)
- [ ] `CognitionDockerSandboxBackend` subclasses `FilesystemBackend` with `virtual_mode=True`; no custom `_resolve_path()` override
- [ ] `LocalSandbox` class in `execution/sandbox.py` removed (or reduced to a deprecated shim if tests depend on it)
- [ ] All existing sandbox tests pass unchanged
- [ ] Unit test: path `/../../etc/passwd` is rejected by `_resolve_path()` for both backends (verifying deepagents' correct check is now in effect)

---

### P3-ALN-2: Remove `ExecutionBackend` Protocol and `ExecutionBackendAdapter`

| Field | Value |
|-------|-------|
| **Layer** | 3 (Execution) |
| **File** | `server/app/execution/backend.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Dependencies** | P3-ALN-1 (removes `LocalExecutionBackend` usage) |

**Problem:** `server/app/execution/backend.py` defines a custom `ExecutionBackend` Protocol (lines 36–119) that is a hand-rolled redeclaration of `deepagents.backends.protocol.SandboxBackendProtocol`. It then defines `ExecutionBackendAdapter` (lines 257–341) which wraps an `ExecutionBackend` back into a `deepagents.SandboxBackendProtocol`-compatible interface — an adapter that converts from a protocol back to the same protocol it mirrors.

The adapter is not used in the live request path. `CognitionDockerSandboxBackend` calls `DockerExecutionBackend.execute()` directly. The `ExecutionBackend` Protocol and `ExecutionBackendAdapter` add ~200 lines of abstraction that deepagents already provides at the protocol level.

**What to keep:** `DockerExecutionBackend` — this is genuine Cognition-specific functionality that deepagents has no equivalent for. It should be retained and made to implement `deepagents.backends.protocol.SandboxBackendProtocol` directly (which it effectively already does via the adapter).

**Fix:**
- Delete `ExecutionBackend` Protocol (lines 36–119) — replace all uses with `deepagents.backends.protocol.SandboxBackendProtocol`
- Delete `ExecutionBackendAdapter` (lines 257–341) — nothing should be routing through it
- Delete `LocalExecutionBackend` (lines 120–255) — superseded by P3-ALN-1's `CognitionLocalSandboxBackend` refactor
- Retain `DockerExecutionBackend`; make it implement `SandboxBackendProtocol` directly
- Update any imports that reference the deleted classes

**Acceptance Criteria:**
- [ ] `ExecutionBackend` Protocol deleted; no references remain
- [ ] `ExecutionBackendAdapter` deleted; no references remain
- [ ] `LocalExecutionBackend` deleted; no references remain
- [ ] `DockerExecutionBackend` type-checks against `deepagents.backends.protocol.SandboxBackendProtocol`
- [ ] `mypy` passes with no new errors
- [ ] All existing backend tests pass (updated to remove tests for deleted classes)

---

### P3-ALN-3: Fix Broken `AgentMiddleware` Import and Tool Name Validation in `cli.py`

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) |
| **File** | `server/app/cli.py` |
| **Status** | **Complete** |
| **Effort** | ~30 minutes |

**Problem 1 — Broken import:** `server/app/cli.py:621` contains:

```python
from deepagents.middleware import AgentMiddleware
```

`deepagents.middleware` does **not** export `AgentMiddleware` in version 0.3.12 (or any known version). The package exports: `FilesystemMiddleware`, `MemoryMiddleware`, `SkillsMiddleware`, `SubAgent`, `SubAgentMiddleware`, `SummarizationMiddleware`. This import raises `ImportError` at runtime whenever a user runs `cognition create middleware <name>`, making the command non-functional.

The correct import — used correctly in `server/app/agent/middleware.py` and `server/app/agent_registry.py` — is:

```python
from langchain.agents.middleware.types import AgentMiddleware
```

**Problem 2 — Invalid tool name accepted silently:** `cognition create tool <name>` transforms the input with `.lower().replace("-", "_").replace(" ", "_")` but never validates that the result is a legal Python identifier. `cognition create tool 123-my-tool` produces a file containing `def 123_my_tool(...)` — a `SyntaxError` that will be silently swallowed at discovery time (caught as a file load error with no user-visible feedback).

**Fix:**
- One-line import correction in `cli.py:621`
- Add `str.isidentifier()` check after name transformation; exit with a clear error if the result is not a valid Python identifier
- Fix misleading "Next steps" text in `cognition create tool` output: remove step 3 ("Use AgentRegistry to register the tool for sessions") — auto-discovery makes this unnecessary and the instruction is incorrect

**Acceptance Criteria:**
- [ ] `cli.py` import corrected to `from langchain.agents.middleware.types import AgentMiddleware`
- [ ] `cognition create middleware test_mw` runs without `ImportError`
- [ ] Generated middleware template contains the correct import line
- [ ] `cognition create tool 123bad` exits with `Error: '123bad' is not a valid Python identifier` before creating any file
- [ ] `cognition create tool my-tool` (transforms to `my_tool`) works correctly — transform then validate
- [ ] "Next steps" output no longer includes the manual `AgentRegistry.register_tool()` instruction

---

## P3-SEC — Security Hardening ✅ 100% Complete

**Must be completed before P3-TR.** The `AgentRegistry` tool loading path (`_load_tools_from_file`) and the `AgentDefinition.tools` import path both execute arbitrary Python in the server process with zero guards. Connecting the registry to the live request path without these fixes would wire a code execution vulnerability directly into every API call.

Security fixes override all priorities per governance rules.

---

### P3-SEC-1: AST Import Scanning Before `exec_module`

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent_registry.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Severity** | CRITICAL |

**Problem:** `_load_tools_from_file()` calls `spec.loader.exec_module(module)` — equivalent to `exec(open(file).read())` — with no pre-execution validation. Any module-level code in a `.py` file dropped into `.cognition/tools/` runs immediately in the server process, which holds all LLM API keys and database credentials in its environment.

**Fix:** Before calling `exec_module`, parse the file with `ast.parse()` and walk the AST, rejecting (or warning loudly on) imports of dangerous stdlib modules: `os`, `subprocess`, `socket`, `ctypes`, `sys`, `shutil`, `importlib`, `pty`, `signal`, `multiprocessing`, `threading`, `concurrent`, `code`, `codeop`, `builtins`. Also flag direct use of `exec`, `eval`, `compile`, and `__import__` as call nodes.

**Behavior options (configurable via settings):**
- `tool_security = "warn"` — log a structured warning, continue loading (default for development)
- `tool_security = "strict"` — refuse to load the file, emit an error event (default for production)

**Acceptance Criteria:**
- [ ] `ast.parse()` called on file contents before `exec_module`
- [ ] Banned import/call detection implemented as an `ast.NodeVisitor`
- [ ] `tool_security` setting added to `Settings` with `"warn"` default
- [ ] In `strict` mode, file is skipped and error is logged; `discover_tools()` continues with remaining files
- [ ] Unit tests: clean file loads, banned import warns/blocks, nested import in function body detected
- [ ] No performance regression on clean files (AST parse of a 200-line file is <1ms)

---

### P3-SEC-2: Protect `.cognition/` from Agent Writes

| Field | Value |
|-------|-------|
| **Layer** | 3 (Execution) |
| **File** | `server/app/execution/backend.py` |
| **Status** | **Complete** |
| **Effort** | ~0.5 days |
| **Severity** | HIGH |

**Problem:** In `local` sandbox mode (the docker-compose default), the AI agent has `write_file` and `execute` access to the entire workspace, including `.cognition/tools/`. A compromised conversation could write a malicious `.py` file there and trigger a hot-reload to execute it in the server process. The path confinement check explicitly *allows* this because `.cognition/tools/` is inside `workspace_path`.

**Fix:** Add a protected-path deny list to `LocalExecutionBackend.write_file()` and `execute()`. Any path resolving under `{workspace_root}/.cognition/` requires explicit opt-in or is blocked entirely. The deny list should be configurable but default to blocking `.cognition/`.

**Acceptance Criteria:**
- [ ] `write_file()` raises `PermissionError` (mapped to a `CognitionError` subclass) for paths under `.cognition/`
- [ ] `execute()` blocks commands that would write to `.cognition/` (e.g. `>` redirection to those paths) — best-effort via path inspection; not a replacement for OS-level controls
- [ ] Protected paths are configurable via settings (`protected_paths: list[str]`)
- [ ] `DockerExecutionBackend` gets the same protection
- [ ] Unit tests: write to `.cognition/tools/evil.py` is blocked; write to `src/foo.py` is allowed

---

### P3-SEC-3: Fix Path Confinement Check (`str.startswith` → `Path.is_relative_to`)

| Field | Value |
|-------|-------|
| **Layer** | 3 (Execution) |
| **Files** | `server/app/execution/backend.py`, `server/app/agent/sandbox_backend.py` |
| **Status** | **Complete** |
| **Effort** | ~0.5 days |
| **Severity** | MEDIUM |

**Problem:** Path traversal confinement uses `str(full_path).startswith(str(self.root_dir))`. This is structurally incorrect: if `root_dir` is `/workspace` and a crafted path resolves to `/workspace-extra/etc/passwd`, the check passes as a false negative because `"/workspace-extra".startswith("/workspace")` is `True`.

**Fix:** Replace all `str.startswith(str(root))` path confinement checks with `Path.is_relative_to(root)` (Python 3.9+, already required by this project).

**Acceptance Criteria:**
- [ ] `backend.py:_resolve_path()` uses `full_path.is_relative_to(self.root_dir)`
- [ ] `sandbox_backend.py` equivalent check updated
- [ ] Unit test: path like `/workspace-extra/secret` is correctly rejected when root is `/workspace`

---

### P3-SEC-4: Harden `AgentDefinition.tools` Module Allowlist

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) / 4 (Agent Runtime) |
| **Files** | `server/app/agent/definition.py`, `server/app/agent/runtime.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Severity** | HIGH |

**Problem:** `AgentDefinition.tools` accepts any dotted string with at least one `.` as a valid tool path (`validate_tools` in `definition.py:119`). `runtime.py` resolves these via `__import__()`, which searches the entire `sys.path`. A YAML file with `tools: ["os.system"]` would give the LLM `os.system` as a callable tool.

**Fix:** Introduce a `trusted_tool_namespaces` setting (default: `["server.app.tools"]`). During `create_agent_runtime()` resolution, reject any `tool_path` whose module component does not start with a trusted namespace. This does not change the existing declarative YAML format — it adds a validation gate at import time.

**Acceptance Criteria:**
- [ ] `trusted_tool_namespaces: list[str]` setting added to `Settings` (default `["server.app.tools"]`)
- [ ] `create_agent_runtime()` validates each tool path against the allowlist before `__import__()`
- [ ] Paths outside the allowlist raise a `CognitionError` subclass (not silently skip)
- [ ] The allowlist is extensible so operators can add their own namespaces
- [ ] Unit tests: `os.system` rejected; `server.app.tools.my_tool` accepted; custom namespace accepted when configured

---

### P3-SEC-5: Tighten CORS Default from Wildcard

| Field | Value |
|-------|-------|
| **Layer** | 6 (API & Streaming) |
| **File** | `server/app/settings.py` |
| **Status** | **Complete** |
| **Effort** | ~0.5 days |
| **Severity** | MEDIUM |

**Problem:** `cors_origins`, `cors_methods`, and `cors_headers` all default to `["*"]` in `Settings`. This means any web page can make cross-origin requests to the Cognition API, enabling CSRF-style attacks.

**Fix:** Change defaults to safe values. `cors_origins` should default to `["http://localhost:3000", "http://localhost:8080"]` (common dev front-end ports) rather than `["*"]`. Document that production deployments must set `COGNITION_CORS_ORIGINS` explicitly. Add a startup warning log when `"*"` is detected.

**Acceptance Criteria:**
- [ ] `cors_origins` default changed from `["*"]` to localhost-only list
- [ ] Startup log emits a warning when `cors_origins` contains `"*"`
- [ ] `COGNITION_CORS_ORIGINS` env var documented in `config.example.yaml`
- [ ] Existing tests updated to explicitly set `cors_origins=["*"]` where needed

---

## P3-TR — Tool Registry End-to-End ✅ 100% Complete

**Blocked on P3-SEC.** The `AgentRegistry` infrastructure (P2-9), file watcher (P2-10), and CLI scaffolding (P2-11) are fully implemented but disconnected from the live request path. `DeepAgentStreamingService.stream_response()` calls `create_cognition_agent()` with no tools — the registry exists but is dead code. This tier wires everything together and adds the API surface to inspect registered tools.

**Partial implementation complete:**
- ✅ P3-TR-1: Fix tool discovery logic in `AgentRegistry`
- ✅ P3-TR-2: Wire `AgentRegistry` into `main.py` Lifespan + File Watcher
- ✅ P3-TR-3: Wire `AgentRegistry` into `stream_response()`
- ✅ P3-TR-4: `GET /tools` and `GET /tools/{name}` API Endpoints
- ✅ P3-TR-5: `ToolSecurityMiddleware` — Runtime Audit Log and Tool Blocklist
- ✅ P3-TR-6: Document and Wire Available Upstream Middleware (complete)
- ✅ P3-TR-7: Surface Tool Load Errors to Users (complete)
- ✅ P3-TR-8: `cognition tools list` and `cognition tools reload` CLI Commands (complete)
- ✅ P3-TR-9: Ensure `.cognition/` Directory Exists Before First Write

---

### P3-TR-1: Fix Tool Discovery Logic in `AgentRegistry`

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent_registry.py` |
| **Status** | **Complete** |
| **Effort** | ~0.5 days |
| **Dependencies** | P3-SEC-1 |

**Problem:** `_load_tools_from_file()` checks `hasattr(obj, "_tool_decorator")` (line 431) — this attribute does not exist on LangChain `@tool`-decorated objects. LangChain's `@tool` produces a `StructuredTool` instance (a `BaseTool` subclass). The second branch `isinstance(obj, BaseTool)` already catches these, making the first branch dead code that adds confusion. Additionally, the lambda closure `factory=lambda obj=obj: obj` returns the same singleton instance every call — fine for stateless tools, but should be documented clearly.

**Fix:**
- Remove the dead `_tool_decorator` branch entirely
- Detect tools via `isinstance(obj, BaseTool)` only (covers both `@tool`-decorated functions and explicit `BaseTool` subclasses)
- Add a module-level `__all__` check: if the tool file defines `__all__`, only inspect those names (respects author intent)
- Document the singleton factory behavior in docstrings

**Acceptance Criteria:**
- [ ] `_tool_decorator` check removed
- [ ] `isinstance(obj, BaseTool)` is the sole detection mechanism
- [ ] `__all__`-aware filtering implemented
- [ ] Existing `test_agent_registry.py` tests updated to use real `@tool` decorated functions
- [ ] New test: file that defines `__all__` only exposes listed tools

---

### P3-TR-2: Wire `AgentRegistry` into `main.py` Lifespan + File Watcher

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) / 4 (Agent Runtime) |
| **Files** | `server/app/main.py`, `server/app/file_watcher.py` |
| **Status** | **Complete** |
| **Effort** | ~0.5 days |
| **Dependencies** | P3-TR-1, P3-SEC-2 |

**Problem:** `initialize_agent_registry()` is never called in `main.py`. The `AgentRegistry` singleton is never set, so `get_agent_registry()` raises `RuntimeError` at every request. The `WorkspaceWatcher` is also never started, so hot-reload is inoperative. Two additional bugs in `file_watcher.py` mean hot-reload would be broken even after wiring:

1. **`asyncio.get_event_loop()` in watchdog's OS thread** (`_schedule_change`, line ~382): watchdog fires events from a background OS thread, not from the asyncio event loop. In Python 3.10+ `get_event_loop()` raises `DeprecationWarning` or `RuntimeError` in a thread with no running loop. The fix is to capture `asyncio.get_event_loop()` once at `start()` time (on the main thread) and use `loop.call_soon_threadsafe(...)` / `asyncio.run_coroutine_threadsafe(coro, loop)` in the callback.

2. **`mark_middleware_pending()` does not exist on `AgentRegistry`** (`_process_change`, line ~439): any file change under `.cognition/middleware/` triggers `self.agent_registry.mark_middleware_pending()`, which raises `AttributeError`. The exception is swallowed by the outer `except`, logged as `"Failed to process file change"`, and middleware hot-reload silently fails every time.

**Fix:** Add to `lifespan()` in `main.py`:
1. Call `initialize_agent_registry(session_manager, settings)` after `initialize_session_manager()`
2. Create and start a `WorkspaceWatcher` pointing at `.cognition/tools/` and `.cognition/middleware/`
3. Stop the watcher in shutdown cleanup

Fix `file_watcher.py`:
- Capture the running event loop at `start()` time; use `loop.call_soon_threadsafe` in `_schedule_change`
- Replace `self.agent_registry.mark_middleware_pending()` with the correct method call (or implement the method on `AgentRegistry` if session-based middleware pending state is desired)

The watcher should only watch directories that actually exist — graceful no-op if `.cognition/tools/` is absent.

**Acceptance Criteria:**
- [ ] `get_agent_registry()` no longer raises `RuntimeError` on a running server
- [ ] `WorkspaceWatcher` started at server boot, stopped at shutdown
- [ ] File drop into `.cognition/tools/` triggers `reload_tools()` within debounce window on Python 3.11/3.12
- [ ] File change in `.cognition/middleware/` does not raise `AttributeError`
- [ ] `_schedule_change` uses `loop.call_soon_threadsafe` — no `asyncio.get_event_loop()` in watchdog thread
- [ ] Server starts cleanly when `.cognition/tools/` does not exist
- [ ] Unit test: lifespan initializes registry and watcher
- [ ] Unit test: `_schedule_change` called from a non-asyncio thread does not raise

---

### P3-TR-3: Wire `AgentRegistry` into `DeepAgentStreamingService.stream_response()`

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) / 5 (LLM Provider) |
| **File** | `server/app/llm/deep_agent_service.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Dependencies** | P3-TR-2 |

**Problem:** `stream_response()` (line 97–103) calls `create_cognition_agent()` with no `tools` argument. Tools discovered from `.cognition/tools/` are never passed to the agent. This is the core wiring gap.

**Fix:** Before calling `create_cognition_agent()`, retrieve the registry and materialise its tools:

```python
from server.app.agent_registry import get_agent_registry

try:
    registry = get_agent_registry()
    custom_tools = registry.create_tools()
except RuntimeError:
    custom_tools = []  # Registry not initialized (test/dev contexts)

agent = create_cognition_agent(
    project_path=project_path,
    model=model,
    store=None,
    checkpointer=checkpointer,
    settings=llm_settings,
    tools=custom_tools if custom_tools else None,
)
```

Tools are global to all sessions (scoping is not per-agent at this tier). Agent cache key already includes `len(tools)`, so adding/removing tools correctly busts the cache.

**Acceptance Criteria:**
- [ ] `create_cognition_agent()` receives tools from the registry on every `stream_response()` call
- [ ] Empty registry (`custom_tools = []`) passes `tools=None` — no behavior change for zero-tool deployments
- [ ] `RuntimeError` from uninitialised registry is caught and degrades gracefully
- [ ] Unit test: mock registry with 2 tools → verify `create_cognition_agent` called with those tools
- [ ] E2E scenario: drop a `@tool`-decorated file in `.cognition/tools/` → start session → agent uses the tool

---

### P3-TR-4: `GET /tools` and `GET /tools/{name}` API Endpoints

| Field | Value |
|-------|-------|
| **Layer** | 6 (API & Streaming) |
| **Files** | `server/app/api/routes/tools.py` (new), `server/app/api/models.py`, `server/app/main.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Dependencies** | P3-TR-2 |

Expose the tool registry via the API, following the same pattern as `GET /agents`.

**New endpoints:**
```
GET  /tools           — list all registered tools
GET  /tools/{name}    — get single tool by name (404 if not found)
```

**Response models:**
```python
class ToolResponse(BaseModel):
    name: str
    source: str        # "programmatic" or absolute file path
    module: str | None # dotted module name if loaded from file

class ToolList(BaseModel):
    tools: list[ToolResponse]
    count: int
```

**Acceptance Criteria:**
- [ ] `GET /tools` returns all registered tools with correct fields
- [ ] `GET /tools/{name}` returns 404 when tool not found
- [ ] Router wired into `main.py` via `app.include_router(tools.router)`
- [ ] Response model added to `api/models.py`
- [ ] Unit tests for both endpoints (empty registry, populated registry, 404)

---

### P3-TR-5: `ToolSecurityMiddleware` — Runtime Audit Log and Tool Blocklist

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent/middleware.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Dependencies** | P3-TR-3 |

**Context:** P3-SEC-1 (AST scanning) is a load-time gate — it inspects Python source before `exec_module()` runs. It cannot prevent a tool that passed scanning from being called at runtime, and it produces no per-invocation visibility. `ToolSecurityMiddleware` is the runtime complement: it intercepts every tool call via `awrap_tool_call`, logs it to the structured log pipeline, and enforces a configurable blocklist as defence-in-depth.

deepagents and langchain have no equivalent — the existing `HumanInTheLoopMiddleware` provides human approval flows, `ToolCallLimitMiddleware` enforces call counts, but neither provides audit logging or a server-side name-based blocklist tied to the session context.

**Implementation:** Subclass `AgentMiddleware`, implement `awrap_tool_call`:

```python
class ToolSecurityMiddleware(AgentMiddleware):
    def __init__(self, blocked_tools: list[str] | None = None) -> None:
        self.blocked_tools: set[str] = set(blocked_tools or [])

    async def awrap_tool_call(self, request, handler):
        tool_name = request.tool.name if request.tool else request.tool_call["name"]
        session_id = getattr(request.runtime.config, "configurable", {}).get("thread_id")

        logger.info("tool_call", tool=tool_name, session_id=session_id,
                    args=request.tool_call.get("args"))

        if tool_name in self.blocked_tools:
            logger.warning("tool_blocked", tool=tool_name, session_id=session_id)
            return ToolMessage(
                content=f"Tool '{tool_name}' is disabled by server policy.",
                tool_call_id=request.tool_call["id"],
                name=tool_name,
                status="error",
            )

        return await handler(request)
```

The `blocked_tools` list is sourced from `Settings.blocked_tools: list[str]` (default `[]`), so operators can disable specific tools via environment variable without code changes.

`ToolSecurityMiddleware` is instantiated in `create_cognition_agent()` alongside `CognitionObservabilityMiddleware` and `CognitionStreamingMiddleware` — it is always active, not opt-in.

**Acceptance Criteria:**
- [ ] `ToolSecurityMiddleware` implemented in `server/app/agent/middleware.py`
- [ ] `Settings.blocked_tools: list[str]` added with default `[]`
- [ ] Every tool call produces a `structlog` `INFO` event with `tool`, `session_id`, `args`
- [ ] Tools in `blocked_tools` return `ToolMessage(status="error")` without calling handler
- [ ] Middleware passed to `create_cognition_agent()` unconditionally
- [ ] Unit tests: allowed tool calls pass through, blocked tool returns error message, args are logged
- [ ] Blocked tool name is case-sensitive (exact match)

---

### P3-TR-6: Document and Wire Available Upstream Middleware

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) / 1 (Foundation) |
| **Files** | `server/app/agent/definition.py`, `server/app/agent/runtime.py`, `config.example.yaml` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Dependencies** | P3-TR-3 |

**Context:** The installed `langchain` package ships four production-quality middleware implementations that are directly relevant to Cognition's use cases and have no equivalent in the current codebase:

| Middleware | Import | Purpose |
|---|---|---|
| `ToolRetryMiddleware` | `langchain.agents.middleware` | Exponential backoff retry on tool failure, per-tool filtering |
| `ToolCallLimitMiddleware` | `langchain.agents.middleware` | Per-tool and global call limits, thread-level and run-level |
| `HumanInTheLoopMiddleware` | `langchain.agents.middleware` | Approve/edit/reject tool calls before execution (uses LangGraph `interrupt()`) |
| `PIIMiddleware` | `langchain.agents.middleware` | Detect and redact PII in messages (email, credit card, IP, custom regex) |

None of these are currently reachable by Cognition users — there is no mechanism to enable them via `AgentDefinition` YAML or `config.yaml` without writing Python. A user who wants retry logic on their custom tools cannot express that today.

**Fix:** Extend `AgentDefinition.middleware` to accept a list of declarative middleware specs alongside raw Python imports. Add a resolver in `create_agent_runtime()` that maps well-known names to their upstream constructors with configurable parameters:

```yaml
# .cognition/agents/my-agent.yaml
middleware:
  - name: tool_retry
    max_retries: 3
    backoff_factor: 2.0
  - name: tool_call_limit
    run_limit: 20
    exit_behavior: continue
  - name: pii
    pii_type: email
    strategy: redact
```

The resolver maps `tool_retry` → `ToolRetryMiddleware(...)`, `tool_call_limit` → `ToolCallLimitMiddleware(...)`, `pii` → `PIIMiddleware(...)`, `human_in_the_loop` → `HumanInTheLoopMiddleware(...)`. Unrecognised names fall back to the existing dotted-import-path resolution.

Also add `config.example.yaml` entries documenting these options, and a note in `AGENTS.md` listing them.

**What this does NOT do:** Does not add new Python — these are wrappers around already-installed code. Does not expose `HumanInTheLoopMiddleware` as a server-side automatic approval mechanism (that requires client-side interrupt handling, which is a separate P4 concern).

**Acceptance Criteria:**
- [ ] `AgentDefinition.middleware` supports both dotted import strings and `{name: ..., **kwargs}` dicts
- [ ] Resolver in `create_agent_runtime()` maps `tool_retry`, `tool_call_limit`, `pii`, `human_in_the_loop` to their langchain constructors
- [ ] Unknown names continue to resolve via dotted import path (no regression)
- [ ] `config.example.yaml` documents all four middleware options with example parameters
- [ ] Unit tests: YAML spec resolves to correct middleware class with correct parameters
- [ ] `mypy` passes — resolver is typed correctly

---

### P3-TR-7: Surface Tool Load Errors to Users

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) / 6 (API & Streaming) |
| **Files** | `server/app/agent_registry.py`, `server/app/llm/deep_agent_service.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Dependencies** | P3-TR-2 |

**Problem:** When a `.cognition/tools/*.py` file fails to load — due to a syntax error, a bad import, a missing dependency, or a failed factory instantiation — the only output is a server-side structlog `ERROR`. The user sees nothing: `tools_discovered` logs `0`, the agent runs without the tool, and there is no indication of what went wrong. On hot-reload triggered by saving a file, the failure is entirely invisible.

This is the single most likely source of "my tool isn't working" confusion in the tool registry UX.

**Fix — two surfaces:**

1. **`_load_tools_from_file()` error accumulation:** Instead of only logging and returning `0`, accumulate errors into a list on the registry instance (`AgentRegistry._load_errors: list[ToolLoadError]`). Each error records `file`, `error_type`, `message`, and `timestamp`.

2. **SSE event on hot-reload failure:** When `reload_tools()` is triggered by the file watcher and a file fails to load, emit a `tool_load_error` SSE event to active sessions via the existing streaming middleware. Format:
   ```json
   {"type": "tool_load_error", "file": "my_tool.py", "error": "SyntaxError: invalid syntax (line 12)"}
   ```

3. **`GET /tools/errors` endpoint:** Returns the accumulated load error list. Cleared on successful reload of the affected file.

**Acceptance Criteria:**
- [ ] `AgentRegistry._load_errors` accumulates `ToolLoadError` entries for each failed file
- [ ] Errors are cleared per-file on successful reload (not globally)
- [ ] `GET /tools/errors` endpoint returns current error list (empty list when no errors)
- [ ] `reload_tools()` emits a `tool_load_error` SSE event for each failed file
- [ ] `spec_from_file_location` returning `None` is no longer a silent failure — logs `WARNING` with file path
- [ ] Unit tests: syntax error in tool file → error recorded in registry → cleared on fix

---

### P3-TR-8: `cognition tools list` and `cognition tools reload` CLI Commands

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) / 6 (API & Streaming) |
| **File** | `server/app/cli.py` |
| **Status** | **Complete** |
| **Effort** | ~1 day |
| **Dependencies** | P3-TR-4 (requires `GET /tools` endpoint) |

**Problem:** There is no CLI surface to inspect or manually trigger the tool registry. A user who drops a file into `.cognition/tools/` and wants to confirm it was loaded has to read server logs and search for `"Tool discovery complete"`. There is no way to force a reload without restarting the server.

**Fix:** Add a `cognition tools` command group with two subcommands:

```
cognition tools list
  — Calls GET /tools, renders a Rich table: name | source file | status
  — Also calls GET /tools/errors and renders any load errors below the table
  — Exit code 1 if any load errors are present (useful for CI)

cognition tools reload
  — Calls POST /tools/reload (new endpoint, triggers reload_tools() server-side)
  — Prints count of tools loaded, lists any errors
  — Exit code 1 if reload produced errors
```

**New API endpoint required:**
```
POST /tools/reload   — triggers AgentRegistry.reload_tools(), returns {count, errors}
```

**Acceptance Criteria:**
- [ ] `cognition tools list` renders a Rich table of registered tools
- [ ] `cognition tools list` renders load errors below the table if any
- [ ] `cognition tools list` exits with code 1 when errors are present
- [ ] `cognition tools reload` triggers server-side reload and reports result
- [ ] `POST /tools/reload` endpoint implemented and wired into `main.py`
- [ ] Both commands handle server-not-running gracefully (clear error, not a traceback)

---

### P3-TR-9: Ensure `.cognition/` Directory Exists Before First Write

| Field | Value |
|-------|-------|
| **Layer** | 6 (API & Streaming) / 1 (Foundation) |
| **Files** | `server/app/api/routes/config.py`, `server/app/agent_registry.py` |
| **Status** | **Complete** |
| **Effort** | ~0.5 days |
| **Dependencies** | None |

**Problem:** Two code paths write to `.cognition/` without ensuring the directory exists first:

1. `config.py` route writes to `.cognition/config.yaml` directly — raises `FileNotFoundError` if `.cognition/` doesn't exist (i.e. user has never run `cognition create tool` or `cognition init`).
2. `initialize_agent_registry()` reads `settings.workspace_path / ".cognition" / "tools"` but does not create it — `discover_tools()` silently returns `0` at `DEBUG` level if absent.

**Fix:**
- `config.py` write path: add `path.parent.mkdir(parents=True, exist_ok=True)` before open
- `initialize_agent_registry()`: create `.cognition/tools/` and `.cognition/middleware/` at startup with `mkdir(parents=True, exist_ok=True)` — this makes the directory structure self-healing and removes the "directory doesn't exist" edge case from the watcher

**Acceptance Criteria:**
- [ ] `PATCH /config` succeeds on a fresh workspace where `.cognition/` does not yet exist
- [ ] Server startup creates `.cognition/tools/` and `.cognition/middleware/` if absent
- [ ] `mkdir` calls use `exist_ok=True` — no error if directories already exist
- [ ] Unit test: config write succeeds when parent directory is absent

---

## P4 — Extended Vision (0% Complete)

Deferred from P3. Unblocked after P3 is complete.

### P4-1: Cloud Execution Backends

| Field | Value |
|-------|-------|
| **Layer** | 3 (Execution) |
| **Status** | **Complete** |
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
| **Status** | **Complete** |
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

- **Unit tests:** 308 passed, 4 skipped, 1 warning
- **Live tests:** 41/41 across 9 phases
  - Phase 1 (Service Health): 9/9
  - Phase 2 (Core API CRUD): 6/6
  - Phase 3 (Agent Streaming): 6/6
  - Phase 4 (MLflow Observability): 3/3
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
| `AgentRegistry` never initialised in `main.py` lifespan | High | Registry infrastructure complete (P2-9) but `get_agent_registry()` raises `RuntimeError` at runtime. Tracked as P3-TR-2. |
| `AgentRegistry` not wired into `stream_response()` | High | Tools discovered from `.cognition/tools/` are never passed to the agent. Tracked as P3-TR-3. |
| `_load_tools_from_file()` dead `_tool_decorator` check | Medium | Detects `BaseTool` instances correctly via second branch, but first branch is dead code that adds confusion. Tracked as P3-TR-1. |
| Tool loading executes arbitrary Python with no guards | Critical | `exec_module()` runs module-level code before any inspection. No AST scanning, no import filtering. Tracked as P3-SEC-1. |
| Agent can write to `.cognition/tools/` and trigger reload | High | Path confinement allows writes inside workspace root, including `.cognition/`. Tracked as P3-SEC-2. |
| Path confinement uses `str.startswith` (false negatives) | Medium | `/workspace-extra` passes check when root is `/workspace`. Affects `CognitionLocalSandboxBackend` and `CognitionDockerSandboxBackend`. Tracked as P3-SEC-3 and P3-ALN-1. |
| `AgentDefinition.tools` accepts any dotted path incl. `os.system` | High | Validation only checks for presence of `.`. `runtime.py` will `__import__` anything. Tracked as P3-SEC-4. |
| CORS defaults to `["*"]` | Medium | All origins allowed by default. Tracked as P3-SEC-5. |
| `CognitionLocalSandboxBackend` reimplements `LocalShellBackend` with regressions | Medium | Removes `virtual_mode` support, introduces buggy `str.startswith` path check. Tracked as P3-ALN-1. |
| `ExecutionBackend` Protocol duplicates `deepagents.SandboxBackendProtocol` | Medium | ~200 lines of adapter indirection unused in live path. Tracked as P3-ALN-2. |
| `cognition create middleware` crashes with `ImportError` | High | `cli.py:621` imports `AgentMiddleware` from `deepagents.middleware` which does not export it. Tracked as P3-ALN-3. |
| Hot-reload never fires on Python 3.11/3.12 | High | `file_watcher._schedule_change` calls `asyncio.get_event_loop()` from watchdog's OS thread — raises `RuntimeError` in Python 3.10+. Tracked as P3-TR-2. |
| Middleware hot-reload always fails with `AttributeError` | High | `file_watcher._process_change` calls `agent_registry.mark_middleware_pending()` which does not exist on `AgentRegistry`. Caught silently. Tracked as P3-TR-2. |
| Tool load errors invisible to users | Medium | Syntax errors, import errors, and missing dependencies in `.cognition/tools/*.py` produce only a server-side structlog `ERROR`. No SSE event, no API response, no CLI feedback. Tracked as P3-TR-7. |
| `cognition create tool` accepts invalid Python identifiers | Medium | Name is transformed but not validated — `123bad` passes through, producing a `SyntaxError` at discovery time that is silently swallowed. Tracked as P3-ALN-3. |
| `cognition create tool` next-steps are misleading | Low | Step 3 instructs users to manually call `AgentRegistry.register_tool()` — auto-discovery makes this unnecessary. Tracked as P3-ALN-3. |
| No `cognition tools list` or `cognition tools reload` commands | Low | No CLI surface to inspect or manually trigger the tool registry. Tracked as P3-TR-8. |
| `.cognition/` not created by `config` route | Low | `config.py` route writes to `.cognition/config.yaml` without creating the parent directory — raises `FileNotFoundError` if the user has not yet run `cognition create tool`. Tracked as P3-TR-9. |
| `tools=[]` and `tools=None` share the same agent cache key | Low | Both map to `"0"` in the MD5 cache key. An empty-registry reload that later succeeds won't bust the cache until at least one tool is present. Tracked as P3-TR-3. |
| File-based tool discovery is untested | Medium | No test writes a `@tool`-decorated `.py` file to disk and calls `discover_tools()`. Detection logic could regress invisibly. Tracked as P3-TR-1. |

### New Testing Tools

| Tool | Purpose | Location |
|------|---------|----------|
| API Proof Script | End-to-end API testing with docker-compose | `scripts/test_docker_compose.sh` |
| | - Tests all 12 API endpoints | |
| | - 9 scenarios covering health, CRUD, SSE, scoping, observability | |
| | - Self-contained (requires only curl + jq) | |
| | - Supports session scoping with automatic header injection | |

---

### Recently Completed
- ✅ P3 Multi-Agent Registry: AgentDefinitionRegistry with built-in `default` and `readonly` agents
- ✅ P3 Markdown + YAML agent file loading with Deep Agents SubAgent TypedDict
- ✅ P3 Session–agent binding with Alembic migration for SQLite and Postgres
- ✅ P3 `GET /agents` and `GET /agents/{name}` endpoints with session creation validation
- ✅ 45 new unit tests for agent registry and 29 E2E scenario tests
- ✅ Subagent-mode validation prevents subagents from being used as session primaries

### Immediate — P3-ALN (Deep Agents Alignment, architecture correction — overrides feature work):
1. P3-ALN-1: Replace `CognitionLocalSandboxBackend` with thin subclass of `LocalShellBackend` (~1 day)
2. P3-ALN-2: Remove `ExecutionBackend` Protocol and `ExecutionBackendAdapter` (~1 day, depends on P3-ALN-1)
3. P3-ALN-3: Fix broken `AgentMiddleware` import in `cli.py` (~15 min, independent)

### Next — P3-SEC (Security Hardening, blocks P3-TR):
1. P3-SEC-1: AST import scanning before `exec_module` in `AgentRegistry` (~1 day) — CRITICAL
2. P3-SEC-2: Protect `.cognition/` from agent writes in `LocalExecutionBackend` (~0.5 days) — HIGH
3. P3-SEC-3: Fix `str.startswith` → `Path.is_relative_to` in path confinement (~0.5 days) — MEDIUM (partially resolved by P3-ALN-1)
4. P3-SEC-4: `AgentDefinition.tools` module namespace allowlist (~1 day) — HIGH
5. P3-SEC-5: Tighten CORS default from `["*"]` (~0.5 days) — MEDIUM

### Then — P3-TR (Tool Registry End-to-End, unblocked after P3-SEC):
1. P3-TR-1: Fix tool discovery logic + add real file-based tests (~0.5 days)
2. P3-TR-2: Wire `AgentRegistry` into `main.py` lifespan + fix `asyncio` thread bug + fix `mark_middleware_pending()` (~1 day)
3. P3-TR-3: Wire `AgentRegistry` into `stream_response()` + fix `tools=[]`/`tools=None` cache key (~1 day)
4. P3-TR-4: `GET /tools` and `GET /tools/{name}` API endpoints (~1 day)
5. P3-TR-5: `ToolSecurityMiddleware` — runtime audit log + tool blocklist via `awrap_tool_call` (~1 day)
6. P3-TR-6: Wire available upstream middleware (`ToolRetryMiddleware`, `ToolCallLimitMiddleware`, `HumanInTheLoopMiddleware`, `PIIMiddleware`) into `AgentDefinition` YAML (~1 day)
7. P3-TR-7: Surface tool load errors via `GET /tools/errors` + SSE event (~1 day)
8. P3-TR-8: `cognition tools list` and `cognition tools reload` CLI commands + `POST /tools/reload` (~1 day)
9. P3-TR-9: Ensure `.cognition/` directory exists before first write (~0.5 days)

### Deferred — P4 Extended Vision:
1. P4-1: Cloud Execution Backends (ECS/Lambda) (~2–4 weeks)
2. P4-2: Ollama Provider + LLM Resilience (~1–2 weeks)

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