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
| **P3** (Full Vision) | 3 | 2/3 | **~75%** |

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

## P3 — Full Vision (~75% Complete)

Advanced features for complete platform vision.

**Note:** Evaluation and feedback are handled by MLflow Native capabilities. Cognition focuses on trace generation; users leverage MLflow's built-in assessment UI and APIs.

### P3-1: Cloud Execution Backends

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

### P3-3: Ollama Provider + LLM Resilience

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

**Immediate — P3 Full Vision:**
1. Wire prompt registry into `create_cognition_agent()` (GUITool was removed — ~3 days)
2. Add API routes for evaluation service (P3-1)
3. Human feedback loop endpoint (Human feedback loop was removed)

**Medium-term:**
4. Cloud execution backends (P3-2)
5. Ollama provider + LLM resilience (P3-3)
6. Dynamic tool validation CLI (Dynamic Tool Validation was removed)

**Long-term:**
7. GUITool base class (P3-3)

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
