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
| **P2** (Robustness + GUI) | 16 | 5/16 | **~31%** |
| — Cleanup (LangGraph Alignment) | 5 | 0/5 | Not Started |
| — Robustness | 7 | 5/7 | ~71% |
| — GUI Extensibility | 4 | 0/4 | Not Started |
| **P3** (Full Vision) | 7 | 0/7 | **~10%** |

**Unit tests:** 223 passed, 2 skipped, 0 warnings (except 1 collection warning)
**Live tests:** 40/41 pass across 9 phases (sole failure: MLflow async tracing — upstream bug)

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
| **Files** | `server/app/observability/__init__.py`, `server/app/observability/mlflow_tracing.py` |

OTel/MLflow gating via settings toggles. `mlflow.langchain.autolog(run_tracer_inline=True)` for async-compatible tracing. Graceful degradation without packages. 12 unit tests.

**Known issue:** MLflow `autolog()` ContextVar propagation fails in async uvicorn contexts (upstream MLflow bug). OpenTelemetry tracing to Jaeger works correctly as alternative.

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

#### P2-CLEANUP-1: Remove Legacy `persistence/` Package

| Field | Value |
|-------|-------|
| **Layer** | 2 (Persistence) |
| **Files to remove** | `server/app/persistence/base.py`, `memory.py`, `sqlite.py`, `factory.py` |
| **Effort** | ~1 day |
| **Status** | Not Started |

The `server/app/persistence/` package is a checkpointer-only wrapper that is a strict subset of the `server/app/storage/` package. Both wrap the same LangGraph checkpointers (`InMemorySaver`, `AsyncSqliteSaver`, `AsyncPostgresSaver`) that Deep Agents accepts via `create_deep_agent(checkpointer=...)`.

**Action:**
- [ ] Remove `server/app/persistence/` package entirely
- [ ] Update `deep_agent_service.py` to pass checkpointer from `StorageBackend` to `create_deep_agent(checkpointer=...)` instead of creating its own
- [ ] Update `agent/runtime.py` to accept checkpointer from caller

#### P2-CLEANUP-2: Wire Unified StorageBackend, Remove Standalone Stores

| Field | Value |
|-------|-------|
| **Layer** | 2 (Persistence) / 6 (API) |
| **Files to remove** | `server/app/session_store.py`, `server/app/message_store.py` |
| **Effort** | ~3 days |
| **Status** | Not Started |

The standalone `session_store.py` and `message_store.py` duplicate the session/message operations already implemented in the unified `StorageBackend` (`storage/sqlite.py`, `storage/postgres.py`). The `StorageBackend` was built as the replacement (P1-1) but never wired into the API routes.

**Action:**
- [ ] Wire `StorageBackend` into API routes (`sessions.py`, `messages.py`, `main.py`)
- [ ] Delete `server/app/session_store.py`
- [ ] Delete `server/app/message_store.py`
- [ ] All persistence goes through one `StorageBackend` instance

**Note:** The custom `messages` table is intentionally kept alongside LangGraph checkpoints. Checkpoints are the source of truth for agent state continuity; the messages table serves API/query needs (pagination, message-level retrieval, enriched metadata like token counts).

#### P2-CLEANUP-3: Consolidate Event Types

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) / 6 (API) |
| **Files affected** | `deep_agent_service.py`, `agent/runtime.py`, `api/models.py` |
| **Effort** | ~1 day |
| **Status** | Not Started |

Event types (`TokenEvent`, `ToolCallEvent`, `DoneEvent`, etc.) are defined in three separate locations. Consolidate to one canonical set in `agent/runtime.py` (the protocol layer). The API layer should only handle serialization.

**Action:**
- [ ] Make `agent/runtime.py` event classes the single source of truth
- [ ] Remove duplicate dataclasses from `deep_agent_service.py`
- [ ] Update `api/models.py` to serialize from the canonical types

#### P2-CLEANUP-4: Replace ContextManager Memory Stubs with Deep Agents/LangGraph Store

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent/context.py` (lines 286-297) |
| **Effort** | ~1 day |
| **Status** | Not Started |

`ContextManager.save_memory()` / `load_memory()` are stubs for a custom memory system. Deep Agents already provides `MemoryMiddleware` for AGENTS.md file loading. For cross-thread persistent memory, LangGraph provides `Store` (via `InMemoryStore`, `PostgresStore`, `RedisStore`).

**Action:**
- [ ] Remove `save_memory()` / `load_memory()` stubs
- [ ] AGENTS.md already handled by Deep Agents `MemoryMiddleware`
- [ ] Pass LangGraph `Store` to `create_deep_agent(store=...)` when cross-thread memory is needed
- [ ] Access store in nodes via Deep Agents `Runtime.store` (injected automatically when context_schema includes store)

#### P2-CLEANUP-5: Cache Compiled Agents Per Session

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/llm/deep_agent_service.py` |
| **Effort** | ~2 days |
| **Status** | Not Started |

Currently `create_cognition_agent()` (which compiles a LangGraph StateGraph) runs on **every message**. The checkpointer provides conversation continuity, but the graph compilation overhead is unnecessary. The `SessionAgentManager` should cache compiled agents and only recompile when tools/middleware change.

**Action:**
- [ ] Cache compiled agent per session in `SessionAgentManager`
- [ ] Recompile only when tool/middleware configuration changes (ties into P2-9)
- [ ] Simplify `SessionAgentManager` to stop duplicating session metadata tracking (already in `StorageBackend`)

---

### Existing Robustness Items

### P2-1: SSE Reconnection ✅

| Field | Value |
|-------|-------|
| **Layer** | 6 (API & Streaming) |
| **File** | `server/app/api/sse.py` |

Event IDs (`{counter}-{uuid}`), `retry:` directive (3000ms), keepalive heartbeat (15s), Last-Event-ID resume, circular event buffer. 38 unit tests.

### P2-2: Circuit Breaker 🔄 PARTIAL

| Field | Value |
|-------|-------|
| **Layer** | 5 (LLM Provider) |
| **File** | `server/app/execution/circuit_breaker.py` |
| **Effort remaining** | ~2 days |

**What exists:** Full implementation (447 lines) — `CircuitBreaker` with CLOSED/OPEN/HALF_OPEN state machine, `RetryWithBackoff` with exponential backoff and jitter, `ResilientProviderClient` combining both, global registry with metrics.

**What's missing:** Not wired into `server/app/llm/provider_fallback.py`. The `ProviderFallbackChain` does not import or use `CircuitBreaker`. The `max_retries` field on `ProviderConfig` is still ignored.

**Remaining work:**
- [ ] Wire `CircuitBreaker` into `ProviderFallbackChain.get_model()`
- [ ] Use `max_retries` from `ProviderConfig`
- [ ] Expose circuit state via `/health` endpoint and Prometheus metrics

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

### P2-8: SessionManager (Thin Facade over Deep Agents/LangGraph)

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) / 6 (API) |
| **File** | `server/app/session_manager.py` (new) |
| **Effort** | ~2 days |
| **Status** | Not Started |
| **Dependencies** | P2-CLEANUP-1, P2-CLEANUP-2 |

**Purpose:** Enable GUI applications to manage sessions across multiple workspaces.

**Deep Agents provides:** `create_deep_agent(checkpointer=..., store=..., context_schema=..., backend=...)`, built-in middleware for AGENTS.md (`MemoryMiddleware`) and skills (`SkillsMiddleware`).

**LangGraph provides:** `thread_id`-based sessions via checkpointer, `Runtime` context injection, `Store` for cross-thread memory.

**What Cognition adds on top:**
- Session lifecycle events (`on_session_created`, `on_session_deleted`) for GUI callbacks
- Cross-workspace session queries (Deep Agents threads are per-graph)
- Per-session agent instances with different tool/middleware stacks
- Session metadata (title, status, workspace_path) that Deep Agents doesn't track

**Acceptance Criteria:**
- [ ] `SessionManager` wraps `StorageBackend` + Deep Agents `create_deep_agent()`
- [ ] Uses LangGraph `thread_id` (via Deep Agents) as the session identifier
- [ ] Uses Deep Agents `context_schema` for `user_id`/`org_id` scoping
- [ ] Session lifecycle events for GUI integration
- [ ] Manages cached compiled agents per session (from P2-CLEANUP-5)

**API Design:**
```python
from cognition import SessionManager, Settings

manager = SessionManager(settings)

# Creates session, maps to Deep Agents/LangGraph thread_id, caches agent
session = await manager.create_session(workspace_path="/project")

# Lifecycle callbacks for GUI
manager.on_session_created(lambda s: gui.add_to_sidebar(s))
manager.on_session_deleted(lambda sid: gui.remove_from_sidebar(sid))
```

### P2-9: AgentRegistry (Per-Session Tool/Middleware Management)

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent_registry.py` (new) |
| **Effort** | ~4 days |
| **Status** | Not Started |

**Purpose:** Enable GUI apps to register tools and middleware that apply per-session. This is genuinely new functionality — Deep Agents' `create_deep_agent()` compiles the graph once, and the compiled graph is immutable.

**Why Deep Agents/LangGraph can't do this:**
- `create_deep_agent()` passes `tools` and `middleware` to `create_agent()` which compiles a LangGraph `StateGraph`
- Tools are baked into `ToolNode` at compile time via `model.bind_tools()`
- Middleware is compiled into graph nodes and edges
- Once compiled, the graph structure is immutable
- No file-based auto-discovery mechanism exists in either framework

**Acceptance Criteria:**
- [ ] `AgentRegistry` class for registering tool/middleware factories
- [ ] Factory pattern — fresh tool/middleware instances per session
- [ ] Auto-discovery from `.cognition/tools/` and `.cognition/middleware/` directories
- [ ] `create_agent_with_extensions()` calls `create_deep_agent()` triggering new graph compilation with updated tools
- [ ] Tools: immediate hot-reload (reload module, recompile graph for new sessions)
- [ ] Middleware: session-based reload (new sessions get updated middleware, existing sessions unchanged)

**API Design:**
```python
from cognition import AgentRegistry
from langchain_core.tools import tool

registry = AgentRegistry()

@tool
def gui_file_picker(description: str) -> str:
    """Open file picker dialog."""
    pass

registry.register_tool("file_picker", lambda: gui_file_picker)
# Calls create_deep_agent() internally with registered tools
agent = registry.create_agent_with_extensions(project_path, settings)
```

### P2-10: File Watcher & Hot-Reload API

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) |
| **File** | `server/app/file_watcher.py` (new) |
| **Effort** | ~3 days |
| **Dependencies** | watchdog library, P2-9 (AgentRegistry) |
| **Status** | Not Started |

**Purpose:** Enable GUI apps to watch workspace files and trigger reloads. LangGraph's CLI has dev-mode hot-reload, but it recompiles the entire graph — no partial reload and no API for GUI integration.

**Acceptance Criteria:**
- [ ] `WorkspaceWatcher` class for monitoring file changes
- [ ] Watch `.cognition/tools/` — triggers `AgentRegistry.reload_tools()` (immediate for new sessions)
- [ ] Watch `.cognition/middleware/` — triggers `AgentRegistry.mark_middleware_pending()` (session-based)
- [ ] Watch `.cognition/config.yaml` — triggers settings reload
- [ ] Callbacks for GUI notifications (`on_tools_changed`, `on_middleware_changed`, `on_config_changed`)

### P2-11: CLI Tool/Middleware Scaffolding

| Field | Value |
|-------|-------|
| **Layer** | 1 (Foundation) |
| **File** | `server/app/cli.py` (extend) |
| **Effort** | ~2 days |
| **Status** | Not Started |

**Purpose:** Help users create tool and middleware templates. LangGraph provides `langgraph new` for project scaffolding, but nothing for individual tool/middleware files.

**Acceptance Criteria:**
- [ ] `cognition tool create <name>` — generates `.cognition/tools/{name}.py` with `@tool` decorator template
- [ ] `cognition middleware create <name>` — generates `.cognition/middleware/{name}.py` with `AgentMiddleware` template
- [ ] Templates include proper imports and structure
- [ ] Automatic directory creation

---

## P3 — Full Vision (~10% Complete)

Advanced features for complete platform vision.

### P3-1: MLflow Evaluation Workflows 🔄 PARTIAL

| Field | Value |
|-------|-------|
| **Layer** | 7 (Observability) |
| **File** | `server/app/evaluation/workflows.py` |
| **Effort remaining** | ~2 weeks |
| **Status** | 40% complete |

**What exists:**
- ✅ `SessionEvaluation` model with feedback, scores, and metrics
- ✅ `EvaluationService` with session run management and multi-scorer evaluation
- ✅ MLflow integration (experiment tracking, metric logging, trace search)
- ✅ Built-in scorers: ToolEfficiency, SafetyCompliance, ResponseQuality

**What's missing:**
- [ ] API routes to expose evaluation service (`POST /sessions/{id}/evaluate`, `GET /sessions/{id}/evaluation`)
- [ ] Persist feedback to database (currently in-memory dict)
- [ ] Human feedback loop endpoint (`POST /sessions/{id}/feedback`)
- [ ] Feedback-annotated traces → evaluation datasets
- [ ] Quality trend dashboards in Grafana
- [ ] CLI command: `cognition eval`

### P3-2: Prompt Registry 🔄 PARTIAL

| Field | Value |
|-------|-------|
| **Layer** | 7 (Observability) / 4 (Agent Runtime) |
| **File** | `server/app/agent/prompt_registry.py` |
| **Effort remaining** | ~3 days |
| **Status** | 60% complete |

**What exists:**
- ✅ `PromptRegistryBackend` protocol
- ✅ `LocalPromptRegistry` — file-based, loads from `.cognition/prompts/`
- ✅ `MLflowPromptRegistry` — uses `mlflow.genai.load_prompt()` API
- ✅ `PromptRegistry` — unified with MLflow-first + local fallback, template formatting, lineage tracking

**What's missing:**
- [ ] Wire into `create_cognition_agent()` — currently the agent factory uses its own hardcoded `SYSTEM_PROMPT` and `settings.llm_system_prompt`, ignoring the registry
- [ ] Support registry references in `AgentDefinition` (e.g., `prompt: "mlflow:security-expert:v1"`)
- [ ] Integration tests with live MLflow server

### P3-3: GUITool Base Class

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent/gui_tool.py` (new) |
| **Effort** | ~2 days |
| **Status** | Not Started |

**Purpose:** Base class for tools that need GUI interaction.

**Acceptance Criteria:**
- [ ] `GUITool` base class with `set_gui_callback()` method
- [ ] `request_gui_action()` method for GUI interaction
- [ ] Documentation for GUI app integration

### P3-4: Dynamic Tool Validation

| Field | Value |
|-------|-------|
| **Layer** | 4 (Agent Runtime) |
| **File** | `server/app/agent/tool_validator.py` (new) |
| **Effort** | ~2 days |
| **Status** | Not Started |

**Purpose:** Validate tools at registration time, not runtime.

**Acceptance Criteria:**
- [ ] `cognition validate` CLI command
- [ ] Check all tools are loadable
- [ ] Verify @tool decorator present
- [ ] Validate middleware inherits from AgentMiddleware
- [ ] Provide helpful error messages with suggestions

### P3-5: Cloud Execution Backends

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

### P3-6: Ollama Provider + LLM Resilience

| Field | Value |
|-------|-------|
| **Layer** | 5 (LLM Provider) |
| **Status** | Not Started |
| **Effort** | ~1-2 weeks |
| **Dependencies** | P2-2 (Circuit Breaker) — partially done |

**Acceptance Criteria:**
- [ ] Ollama provider factory registered
- [ ] Streaming-level fallback: mid-stream provider failure triggers fallback
- [ ] Provider factories return typed protocol, not `Any`
- [ ] Gateway integration option (MLflow AI Gateway or LiteLLM)

### P3-7: Human Feedback Loop

| Field | Value |
|-------|-------|
| **Layer** | 7 (Observability) / 6 (API & Streaming) |
| **Status** | Not Started |
| **Effort** | ~1-2 weeks |
| **Dependencies** | P3-1 (MLflow Evaluation Workflows) |

**Acceptance Criteria:**
- [ ] `POST /sessions/{id}/feedback` endpoint
- [ ] Feedback attached to MLflow traces
- [ ] Feedback-annotated traces become evaluation datasets
- [ ] Feedback-based filtering in MLflow UI

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

### Known Issues

| Issue | Severity | Status |
|-------|----------|--------|
| MLflow `autolog()` ContextVar error in async contexts | Medium | Upstream MLflow bug. `run_tracer_inline=True` applied but insufficient. OTel tracing works as alternative. |
| Circuit breaker not wired into LLM fallback chain | Medium | Implementation exists, integration pending (P2-2) |
| Prompt registry not wired into agent factory | Low | Implementation exists, integration pending (P3-2) |
| Evaluation feedback stored in-memory only | Low | Needs persistence backend (P3-1) |
| Legacy `persistence/` package duplicates `storage/` | Medium | Remove and consolidate (P2-CLEANUP-1) |
| `session_store.py` / `message_store.py` not using unified StorageBackend | Medium | Wire StorageBackend into routes (P2-CLEANUP-2) |
| Event types defined in 3 places | Low | Consolidate to `agent/runtime.py` (P2-CLEANUP-3) |
| Agent recompiled on every message (no caching) | Medium | Cache per session (P2-CLEANUP-5) |

---

## Next Steps

**Immediate — Deep Agents / LangGraph Alignment Cleanup (do first):**
1. Remove legacy `persistence/` package (P2-CLEANUP-1 — ~1 day)
2. Wire `StorageBackend` into API routes, delete standalone stores (P2-CLEANUP-2 — ~3 days)
3. Cache compiled agents per session (P2-CLEANUP-5 — ~2 days)
4. Consolidate event types (P2-CLEANUP-3 — ~1 day)

**Short-term — GUI Extensibility:**
5. SessionManager as thin facade over LangGraph threads (P2-8 — ~2 days)
6. AgentRegistry for per-session tool/middleware management (P2-9 — ~4 days)
7. Wire circuit breaker into `ProviderFallbackChain` (P2-2 — ~2 days)

**Medium-term — Developer Experience:**
8. File Watcher & Hot-Reload API (P2-10 — ~3 days)
9. CLI Tool/Middleware Scaffolding (P2-11 — ~2 days)
10. Wire prompt registry into `create_cognition_agent()` (P3-2 — ~3 days)

**Long-term — Full Vision:**
11. Add API routes for evaluation service (P3-1)
12. Cloud execution backends (P3-5)
13. Human feedback loop endpoint (P3-7)

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
