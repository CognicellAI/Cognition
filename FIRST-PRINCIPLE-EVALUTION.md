# Cognition First Principles Evaluation

## Purpose

This document evaluates Cognition against a first-principles architecture for a "batteries-included" AI application backend -- a system where you define your agent (tools, system prompt, skills) and get everything else out of the box: API, streaming, persistence, sandboxing, observability, multi-user scoping, and evaluation.

The analogy is Payload CMS: you define your collections, Payload gives you the database, admin UI, auth, hooks, and REST/GraphQL APIs. Cognition should do the same for AI agents.

---

## The Ideal Architecture

### Design Principles

1. **Declarative agent definition.** You describe your agent as a schema (tools, prompt, skills, middleware). Cognition instantiates and manages it.
2. **Pluggable persistence.** Abstract interface with SQLite for development, Postgres for production. Sessions, messages, and checkpoints unified behind one interface.
3. **Pluggable execution.** Abstract sandbox protocol with implementations for local, Docker, and cloud services (ECS, Lambda, K8s). The same agent code runs in any environment.
4. **Deep Agents as blessed default.** The agent runtime is opinionated (Deep Agents / LangGraph) but exposed through a thin protocol so migration is bounded.
5. **Built-in LLM provider management.** Provider registry with fallback chain. Optional gateway integration for advanced routing.
6. **Headless backend.** No UI. The REST/SSE API is the product. MLflow UI for observability. Custom UIs built by developers.
7. **Native observability.** MLflow's first-party Deep Agent integration for tracing and evaluation. OTel for operational metrics. Prometheus for system monitoring.
8. **Minimal multi-user harness.** Trusted proxy model with `X-User-ID` header. Session-scoped user isolation. Fail-closed when multi-user is enabled. No auth system -- that belongs in the gateway.

### Architectural Layers

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 7: OBSERVABILITY                                          │
│  MLflow native deepagents tracing, evaluation, prompt registry   │
│  OTel for operational metrics, Prometheus for system monitoring   │
└──────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────┐
│  Layer 6: API & STREAMING                                        │
│  FastAPI REST + SSE, session management, multi-user harness      │
│  Rate limiting, CORS, security headers                           │
└──────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────┐
│  Layer 5: LLM PROVIDER                                           │
│  Provider registry + fallback chain, per-session model override  │
│  Optional MLflow AI Gateway / LiteLLM integration                │
└──────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────┐
│  Layer 4: AGENT RUNTIME                                          │
│  Deep Agents (blessed default) behind AgentRuntime protocol      │
│  Declarative agent definition, middleware, lifecycle management   │
└──────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────┐
│  Layer 3: EXECUTION                                              │
│  Pluggable sandbox: Local, Docker, ECS, Lambda, K8s              │
│  Container lifecycle, workspace mounting, network isolation       │
└──────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────┐
│  Layer 2: PERSISTENCE                                            │
│  Unified interface: sessions + messages + checkpoints            │
│  SQLite (dev), Postgres (prod), Alembic migrations               │
└──────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1: FOUNDATION                                             │
│  Domain models (Pydantic V2), exception hierarchy, config system │
│  Zero internal dependencies, pure data structures                │
└──────────────────────────────────────────────────────────────────┘
```

**Dependency direction:** Strictly top-down. Each layer depends only on layers below it.

---

## Gap Analysis: Current Codebase vs Ideal Architecture

### Overall Completion: ~55-60%

The codebase has a functional happy path (create session, send message, stream SSE response with tools) and solid architectural scaffolding. The gaps are concentrated in production-readiness: persistence durability, execution isolation, multi-user support, observability depth, and resilience patterns.

---

### Layer 1: Foundation

**Completion: ~75%**

#### What Exists

**Domain models** (`server/app/models.py`, 112 lines):
- `Session` (dataclass) -- well-defined with id, workspace_path, title, thread_id, status, config, timestamps, message_count. Includes `to_dict()` / `from_dict()` serialization.
- `Message` (dataclass) -- minimal: id, session_id, role, content, parent_id, created_at.
- `SessionConfig` (Pydantic BaseModel) -- provider, model, temperature, max_tokens, system_prompt.
- `ExecutionResult` (dataclass) -- output, exit_code, duration_ms.
- `SessionStatus` (str Enum) -- ACTIVE, INACTIVE, ERROR.

**Exception hierarchy** (`server/app/exceptions.py`, 177 lines):
- `CognitionError` base with `to_dict()` JSON serialization.
- Subclasses: `SessionError`, `LLMError`, `ToolError`, `ProjectError`, `ValidationError`, `RateLimitError`.
- Concrete exceptions: `SessionNotFoundError`, `SessionLimitExceededError`, `LLMUnavailableError`, `LLMRateLimitError`, `ToolExecutionError`, `ProjectNotFoundError`.
- `ErrorCode` enum with 15 codes.

**Configuration** (`server/app/settings.py`, 200 lines; `server/app/config_loader.py`, 589 lines):
- Pydantic V2 `BaseSettings` with `SettingsConfigDict`.
- `SecretStr` for API keys (OpenAI, AWS, OpenAI-compatible).
- Field validators for port, max_sessions, timeout.
- Four-layer hierarchy: built-in defaults, global YAML, project YAML, environment variables.
- Auto-generated config example from Settings schema introspection.
- `ConfigLoader` class with caching, dot-notation access, env var export.

#### What's Missing

| Gap | Impact | Effort |
| --- | --- | --- |
| **No `AgentDefinition` model.** No declarative Pydantic model that captures an agent's full definition (tools, middleware, skills, subagents, interrupt_on). Agent creation is imperative kwargs, not a validated schema. This is the Payload "collection schema" equivalent -- the most important missing model. | High | ~100 lines |
| **`Message` model is anemic.** No fields for tool_calls, tool_call_id, token_count, model_used, metadata, attachments. | Medium | ~30 lines |
| **`Session` uses dataclass, not Pydantic.** Manual `to_dict()` / `from_dict()` instead of Pydantic's `.model_dump()` / `.model_validate()`. Bypasses Pydantic V2 validation on the core domain model. Inconsistent with `SessionConfig` which is Pydantic. | Low | ~50 lines |
| **4 exception codes without corresponding classes.** `SESSION_EXPIRED`, `LLM_TIMEOUT`, `TOOL_NOT_FOUND`, `TOOL_TIMEOUT` codes exist in the enum but have no exception subclasses. | Low | ~40 lines |
| **Timestamp inconsistency.** `Session.created_at` / `updated_at` are `str`. `Message.created_at` is `datetime`. | Low | ~20 lines |

**Estimated effort to close: 2-3 days.**

---

### Layer 2: Persistence

**Completion: ~40%**

This is the weakest layer relative to the ideal architecture.

#### What Exists

**Abstract checkpointer interface** (`server/app/persistence/base.py`, 30 lines):
- `PersistenceBackend` ABC with `get_checkpointer()` and `close()`.
- This covers LangGraph checkpointing only. It does NOT cover session storage or message storage.

**SQLite checkpointer** (`server/app/persistence/sqlite.py`, 59 lines):
- `SqliteBackend` wrapping LangGraph's `AsyncSqliteSaver`. Lazy init, directory creation, cleanup.

**Memory checkpointer** (`server/app/persistence/memory.py`, 25 lines):
- `MemoryBackend` wrapping `MemorySaver`. Trivial.

**Factory** (`server/app/persistence/factory.py`, 34 lines):
- `create_persistence_backend()` dispatches on settings to sqlite or memory. Silently falls back to sqlite for unknown types (including "postgres").

**Session store** (`server/app/session_store.py`, 296 lines):
- `SqliteSessionStore` with full CRUD (create, get, list, update, update_message_count, delete).
- Opens a new `aiosqlite.connect()` per operation (7 occurrences). No connection pooling.
- Global `_store_cache` dict for per-workspace store instances.

**Messages** (`server/app/api/routes/messages.py:47`):
- Module-global Python dict: `_messages: dict[str, list[MessageResponse]] = {}`.
- Comment: "In-memory message store (replace with database in production)."

#### What's Missing

| Gap | Impact | Effort |
| --- | --- | --- |
| **Messages are not persisted.** Server restart loses all conversation history. This is the single most critical production gap. | Critical | ~200 lines |
| **No unified persistence interface.** `PersistenceBackend` only covers checkpointing. Session storage (`SqliteSessionStore`) is a separate, non-pluggable concrete class. Message storage is a dict. There is no single interface that unifies session store + message store + checkpointer. | High | ~200 lines |
| **No Postgres implementation.** Settings accept `"postgres"` but the factory silently falls back to SQLite. Zero Postgres code exists. | High | ~300 lines + deps |
| **No Alembic migrations.** Schema is `CREATE TABLE IF NOT EXISTS` inline SQL. Schema evolution is impossible without manual intervention. | High | ~150 lines + config |
| **No connection pooling.** Every `SqliteSessionStore` method opens a fresh `aiosqlite.connect()`. Tolerable for SQLite, critical for Postgres. | Medium | ~100 lines |
| **Session store is not pluggable.** `SqliteSessionStore` is a concrete class imported directly. No abstract `SessionStore` protocol. | Medium | ~50 lines |
| **No `MessageStore` abstraction.** Messages have no storage interface at all. | Medium | ~50 lines |

**Estimated effort to close: 2-3 weeks.**

---

### Layer 3: Execution

**Completion: ~45%**

#### What Exists

**Local sandbox** (`server/app/sandbox.py`, 103 lines):
- `LocalSandbox` with `execute(command, timeout, env)`.
- Synchronous `subprocess.run()` with `shell=True` (line 64).
- Timeout support, combined stdout/stderr capture, working directory isolation.

**Sandbox backend** (`server/app/agent/sandbox_backend.py`, 85 lines):
- `CognitionLocalSandboxBackend` inherits from `deepagents.FilesystemBackend` and implements `deepagents.SandboxBackendProtocol`.
- Delegates file operations (ls, read, write, edit, glob, grep) to FilesystemBackend.
- Delegates command execution to `LocalSandbox`.
- Path traversal protection via `_resolve_path()`.
- Output truncation at 100KB.

**Dockerfile** (`Dockerfile`, 69 lines):
- Multi-stage build for the server. NOT for per-session containers.

#### What's Missing

| Gap | Impact | Effort |
| --- | --- | --- |
| **No Docker per-session sandbox.** The core value proposition ("Container-per-session with optional network isolation") does not exist. The only execution path is local subprocess on the server host. | Critical | ~400-500 lines |
| **`shell=True` security violation.** `sandbox.py:64` uses `subprocess.run(command, shell=True, ...)` despite AGENTS.md explicitly prohibiting it. Shell injection is possible. | Critical | ~20 lines (but requires argument parsing changes) |
| **No Cognition-owned sandbox protocol.** The sandbox interface is `deepagents.SandboxBackendProtocol`, not a Cognition-owned abstraction. Adding a Docker or cloud backend requires implementing a deepagents protocol, not a Cognition protocol. | High | ~50 lines |
| **No cloud sandbox backends.** No ECS, Lambda, K8s, E2B, or Fly.io implementations. | High | ~300-500 lines per backend |
| **No network isolation.** Zero network policy enforcement. Agent subprocesses have full network access. | High | ~150 lines |
| **Synchronous execution.** `LocalSandbox.execute()` is synchronous, blocking the FastAPI async event loop. | Medium | ~100 lines |
| **No sandbox lifecycle management.** No create/destroy/list/timeout APIs for sandboxes. No resource limits (CPU, memory, disk). | Medium | ~200 lines |

**Estimated effort to close: 3-4 weeks** (Docker backend + security fixes + protocol ownership). Cloud backends are additional.

---

### Layer 4: Agent Runtime

**Completion: ~65%**

This is the most functional layer because Deep Agents does the heavy lifting.

#### What Exists

**Agent factory** (`server/app/agent/cognition_agent.py`, 138 lines):
- `create_cognition_agent()` -- well-parameterized factory accepting project_path, model, store, checkpointer, system_prompt, memory, skills, subagents, interrupt_on, middleware, tools, settings.
- Calls `deepagents.create_deep_agent()` with all parameters.
- Default system prompt with planning instructions.

**Middleware** (`server/app/agent/middleware.py`, 85 lines):
- `CognitionObservabilityMiddleware` -- Prometheus metrics for LLM call duration and tool call counts.
- `CognitionStreamingMiddleware` -- dispatches "thinking"/"idle" status events.
- Both implement `langchain.agents.middleware.types.AgentMiddleware`.

**Context management** (`server/app/agent/context.py`, 301 lines):
- `ProjectIndex` -- file metadata index with importance scoring.
- `FileRelevanceScorer` -- pattern-based file relevance.
- `ContextManager` -- builds project indexes, selects relevant files, formats context for LLM.

**Streaming service** (`server/app/llm/deep_agent_service.py`, 474 lines):
- `DeepAgentStreamingService` -- full streaming pipeline using `agent.astream_events()` v2.
- 9 event types: Token, ToolCall, ToolResult, Usage, Done, Error, Planning, StepComplete, Status.
- `SessionAgentManager` -- per-session service registry.
- Cost estimation by provider.

#### What's Missing

| Gap | Impact | Effort |
| --- | --- | --- |
| **No `AgentRuntime` protocol.** `create_cognition_agent()` returns `Any`. There is no Cognition-owned interface for the agent runtime. Swapping to raw LangGraph or another framework requires rewriting the factory and streaming service. | High | ~100 lines |
| **No declarative agent definition.** Configuration is passed as kwargs, not as a validated `AgentDefinition` schema. This is the "you define your agent, Cognition does the rest" story -- and the definition part is missing. | High | ~100 lines (coupled with Layer 1) |
| **No agent lifecycle management.** No pause, resume, or graceful cancellation. The abort endpoint (`sessions.py:252`) returns `{"success": True}` without cancelling anything. | Medium | ~200 lines |
| **ContextManager is not integrated.** Exported in `__init__.py` but never called by the agent factory or streaming service. Built and unused. | Medium | ~50 lines |
| **Memory is stubbed.** `save_memory()` / `load_memory()` in context.py are no-ops. Comment says "This would integrate with StoreBackend if available." | Medium | ~150 lines |
| **Tight coupling to deepagents.** The factory calls `deepagents.create_deep_agent()` directly. The sandbox backend implements `deepagents.SandboxBackendProtocol`. The streaming service parses LangGraph internal event strings. | Structural | Architectural refactor |

**Estimated effort to close: 2-3 weeks** (protocol + lifecycle + integration). Decoupling from deepagents is a longer effort bounded by the protocol layer.

---

### Layer 5: LLM Provider

**Completion: ~70%**

This is the best-designed layer.

#### What Exists

**Provider registry** (`server/app/llm/registry.py`, 92 lines):
- 4 providers: `openai`, `openai_compatible`, `bedrock`, `mock`.
- `register_provider(name, factory)` / `get_provider_factory(name)` -- clean plugin system.
- Each factory creates a LangChain `ChatModel` (ChatOpenAI, ChatBedrock, MockLLM).

**Fallback chain** (`server/app/llm/provider_fallback.py`, 209 lines):
- `ProviderFallbackChain` with priority-ordered providers.
- `get_model()` tries providers in order, catches exceptions, falls back.
- `ProviderConfig` with provider, model, priority, enabled, max_retries, api_key, base_url, region.
- `from_settings()` classmethod to build chain from app settings.

**Discovery** (`server/app/llm/discovery.py`, 127 lines):
- `DiscoveryEngine` probes all providers concurrently.
- Probes OpenAI-compatible via `/models` endpoint, static lists for OpenAI/Bedrock.
- Reverse lookup: `get_provider_for_model()`.

**Mock** (`server/app/llm/mock.py`, 232 lines):
- Full `BaseChatModel` implementation with pattern-based responses, `ainvoke`, `astream`, `bind_tools`.

#### What's Missing

| Gap | Impact | Effort |
| --- | --- | --- |
| **No Ollama provider.** Settings define `ollama_model` and `ollama_base_url` but no factory is registered. | Medium | ~30 lines |
| **No circuit breaker.** Docstring claims "Integrates with the circuit breaker pattern" but there is no circuit breaker. `max_retries` field on `ProviderConfig` is never read or used. | Medium | ~150 lines |
| **No retry logic.** `get_model()` makes one attempt per provider with no retries despite `max_retries` existing. | Medium | ~50 lines |
| **No proper token counting.** Cost estimation uses `len(content.split())` which is off by 2-4x from actual token counts. | Medium | ~50 lines |
| **No gateway integration.** MLflow AI Gateway and LiteLLM described in docs but not implemented. | Low | ~100 lines |
| **Provider factories return `Any`.** No protocol constraint on what a model must support. | Low | ~30 lines |
| **No streaming-level fallback.** Fallback operates at model creation, not during streaming. Mid-stream provider failure has no recovery. | Low | ~200 lines |

**Estimated effort to close: 1-2 weeks.**

---

### Layer 6: API & Streaming

**Completion: ~60%**

#### What Exists

**REST endpoints** (3 route files, ~635 lines total):
- Sessions: POST, GET, GET-by-id, PATCH, DELETE, POST abort. Full CRUD.
- Messages: POST (returns SSE stream), GET list (paginated), GET by id.
- Config: GET merged config with live model discovery.
- Health: `/health` and `/ready` probes.

**SSE streaming** (`server/app/api/sse.py`, 166 lines):
- `SSEStream` with event formatting, generator, and `StreamingResponse` creation.
- `EventBuilder` with typed factory methods for all 9 event types.
- Client disconnection detection.
- Anti-buffering header (`X-Accel-Buffering: no`).

**API models** (`server/app/api/models.py`, 218 lines):
- Pydantic V2 models for all request/response types.
- `SessionResponse.from_core()` bridge from domain to API model.

**HTTP middleware** (`server/app/middleware.py`, 101 lines):
- `ObservabilityMiddleware` -- request count + duration Prometheus metrics.
- `SecurityHeadersMiddleware` -- X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Referrer-Policy.

**Rate limiter** (`server/app/rate_limiter.py`, 153 lines):
- `TokenBucket` algorithm with burst support and per-key buckets.
- Started in server lifespan.

#### What's Missing

| Gap | Impact | Effort |
| --- | --- | --- |
| **No multi-user harness.** Zero references to `X-User-ID`, `user_id`, or any multi-user concept. No session ownership. No per-user isolation. No fail-closed middleware. | Critical | ~300 lines |
| **Rate limiter is dead code.** Started in lifespan but `check_rate_limit()` is never called from any endpoint or middleware. | High | ~30 lines |
| **Abort is a stub.** Returns `{"success": True}` without cancelling anything. Comment: "In a real implementation..." | High | ~100 lines |
| **No SSE reconnection.** No `id:` field in events for `Last-Event-ID` resumption. No `retry:` directive. No keepalive heartbeat. Connection drop loses all progress. | Medium | ~100 lines |
| **No CORS middleware.** No `CORSMiddleware` despite being a web API consumed by browser-based UIs. | Medium | ~10 lines |
| **No WebSocket endpoint.** `websockets>=13.0` is a dependency but no WebSocket routes exist. | Low | Optional |
| **No OpenAPI security schemes.** No API key or bearer token scheme definitions. | Low | ~20 lines |

**Estimated effort to close: 2-3 weeks.**

---

### Layer 7: Observability

**Completion: ~50%**

#### What Exists

**Observability module** (`server/app/observability/__init__.py`, 319 lines):
- Structured logging via `structlog` with JSON/console output.
- Prometheus metrics: request count, request duration, LLM call duration, tool call count, session count (5 metrics, 4 active).
- OpenTelemetry tracing: TracerProvider with OTLP exporter, FastAPI auto-instrumentation, LangChain auto-instrumentation.
- `traced()` decorator and `span()` context manager for manual instrumentation.
- `timed()` decorator for Histogram recording.
- Graceful degradation: all OTel/Prometheus imports wrapped in try/except.

**Docker Compose observability stack** (`docker-compose.yml`):
- Prometheus, Grafana, Jaeger, Loki, Promtail.
- Referenced config files for each service.

**Agent middleware metrics** (`server/app/agent/middleware.py`):
- LLM call duration tracking.
- Tool call success/error counting.

#### What's Missing

| Gap | Impact | Effort |
| --- | --- | --- |
| **No MLflow integration in code.** An extensive interoperability doc exists (`docs/v2/guides/mlflow-interoperability.md`, 575+ lines) but zero lines of MLflow code in the server. No `mlflow` in dependencies. No `MLflowTracingMiddleware`. No `mlflow_enabled` setting. MLflow has a native Deep Agent integration that would work with one line of code. | High | ~20 lines (Phase 1) to ~500 lines (all phases) |
| **No evaluation capability.** Zero code for scoring, benchmarking, or evaluating agent quality. This is the biggest capability gap in the platform. | High | ~300-500 lines |
| **`SESSION_COUNT` metric is dead.** Defined but never incremented. Session lifecycle events are not tracked. | Low | ~5 lines |
| **`traced()` and `timed()` decorators unused.** Exist as utilities but not applied to any function. All tracing is auto-instrumentation only. | Low | ~20 lines to wire up |
| **No deep health checks.** `/health` returns session count but does not probe database, LLM providers, or disk. | Low | ~50 lines |
| **No request correlation IDs.** No trace ID propagation in HTTP responses or SSE events. | Low | ~30 lines |
| **No alerting rules.** No Prometheus alerting rules or Grafana alert definitions. | Low | ~50 lines |

**Estimated effort to close: 1-2 weeks** (MLflow Phase 1 + wiring existing code). Full MLflow integration (all phases) and evaluation framework: ~4-6 weeks additional.

---

## Summary

### Completion by Layer

| Layer | Ideal | Current | Gap | Effort |
| --- | --- | --- | --- | --- |
| 1. Foundation | Declarative agent schema, rich models, complete exceptions | Good config, decent models, missing AgentDefinition | **25%** | 2-3 days |
| 2. Persistence | Unified pluggable interface, Postgres, migrations | Checkpointer ABC exists, SQLite works, messages in-memory | **60%** | 2-3 weeks |
| 3. Execution | Docker/cloud sandboxes, network isolation, lifecycle | Local subprocess only, shell=True, no isolation | **55%** | 3-4 weeks |
| 4. Agent Runtime | Protocol-backed, declarative, with lifecycle management | Functional via Deep Agents, no protocol, no cancel | **35%** | 2-3 weeks |
| 5. LLM Provider | Registry + fallback + retries + gateway | Registry + fallback work, no retries/circuit breaker | **30%** | 1-2 weeks |
| 6. API & Streaming | Multi-user, SSE reconnection, rate limiting | CRUD works, SSE works, no user scoping, dead rate limiter | **40%** | 2-3 weeks |
| 7. Observability | MLflow native + evaluation + deep health | OTel + Prometheus exist, MLflow is doc-only, no evaluation | **50%** | 1-6 weeks |

### What Works Today

The happy path is functional:
1. Start the server in a workspace directory
2. Create a session via `POST /sessions`
3. Send a message via `POST /sessions/{id}/messages`
4. Receive SSE stream with token, tool_call, tool_result, planning, usage, done events
5. Agent reasons, calls tools (file read/write/edit, shell execute, glob, grep), and completes multi-step tasks
6. Session state survives server restarts via LangGraph checkpoints

The configuration system, provider registry, fallback chain, SSE event taxonomy, and error hierarchy are well-designed.

### What Doesn't Work Today

1. **Messages are lost on server restart** (in-memory dict)
2. **All sessions are visible to all callers** (no user scoping)
3. **Agent commands run with `shell=True`** on the server host (no isolation)
4. **Abort does nothing** (stub endpoint)
5. **Rate limiter is never checked** (dead code)
6. **Postgres is accepted in config but silently ignored** (falls back to SQLite)
7. **MLflow integration is documentation only** (zero code)
8. **Docker/cloud sandbox backends don't exist** (local subprocess only)
9. **No evaluation of agent quality** (no capability at all)

### Critical Path to "Batteries Included"

If I were building this from first principles to reach the Payload CMS level of "it just works," the priority order would be:

| Priority | Work | Why | Duration |
| --- | --- | --- | --- |
| **P0** | Message persistence | Cannot ship a product that loses conversation history | 1 week |
| **P0** | Fix `shell=True` | Active security vulnerability | 1 day |
| **P0** | Multi-user harness (X-User-ID + session scoping) | Cannot deploy for more than one person | 1 week |
| **P1** | Unified persistence interface + Postgres | Required for any production deployment | 2 weeks |
| **P1** | Docker per-session sandbox | The core differentiation -- secure execution | 2-3 weeks |
| **P1** | `AgentDefinition` model + declarative creation | The "define your agent, get everything" story | 1 week |
| **P1** | MLflow integration (Phase 1: autolog) | One line of code, massive observability gain | 1 day |
| **P2** | Wire rate limiter to routes | Dead code that should be alive | 1 day |
| **P2** | Implement abort (asyncio task cancellation) | Users need to stop runaway agents | 1 week |
| **P2** | SSE reconnection (event IDs + keepalive) | Required for reliable long-running sessions | 1 week |
| **P2** | AgentRuntime protocol | Insurance against Deep Agents dependency risk | 1 week |
| **P2** | Alembic migrations | Required before any schema change | 3 days |
| **P2** | CORS middleware | Required for browser-based UIs | 1 hour |
| **P3** | Ollama provider + circuit breaker + retries | LLM resilience | 1-2 weeks |
| **P3** | MLflow evaluation pipeline | Systematic quality assessment | 2-3 weeks |
| **P3** | MLflow middleware + prompt registry | Rich tracing + prompt versioning | 2 weeks |
| **P3** | Cloud sandbox backends (ECS/Lambda/K8s) | Enterprise deployment flexibility | 2-4 weeks per backend |
| **P3** | Human feedback loop | Closes the improvement cycle | 1-2 weeks |

### Total Estimated Effort

- **P0 (table stakes):** ~2-3 weeks
- **P1 (production-ready):** ~6-8 weeks
- **P2 (robust):** ~4-5 weeks
- **P3 (full vision):** ~10-16 weeks

**Total to reach "batteries included" parity with the documented vision: approximately 5-8 months of focused engineering.**

The current codebase provides a meaningful head start. The configuration system, provider registry, SSE streaming pipeline, exception hierarchy, and agent factory are solid foundations. The persistence layer, execution layer, and multi-user support require the most work. The observability layer is the easiest win -- MLflow's native Deep Agent integration can be enabled in one day.

---

## Key Design Decisions

These decisions should be made before implementation begins:

### 1. Own the sandbox protocol

The current sandbox backend implements `deepagents.SandboxBackendProtocol`. Cognition should define its own `ExecutionBackend` protocol and adapt Deep Agents to it, not the other way around. This is the foundation for pluggable Docker/cloud backends.

### 2. Unify persistence behind one interface

The current codebase has three separate persistence paths: `PersistenceBackend` (checkpoints), `SqliteSessionStore` (sessions), and `_messages` dict (messages). These should be unified behind a single `StorageBackend` protocol with `sessions`, `messages`, and `checkpoints` sub-interfaces.

### 3. Make agent definition declarative

The Payload CMS equivalent is: you write a collection schema, Payload gives you everything. The Cognition equivalent should be: you write an `AgentDefinition` (tools, prompt, skills, middleware), Cognition gives you the API, streaming, persistence, and sandbox. The `AgentDefinition` model is the developer-facing API surface.

### 4. Deep Agents is default, not required

Define an `AgentRuntime` protocol. Deep Agents is the blessed implementation. The protocol exists as insurance -- not for users to plug in CrewAI, but so the Cognition team can migrate to raw LangGraph if Deep Agents stalls or breaks.

### 5. MLflow is the observability layer, not an optional integration

MLflow's native Deep Agent tracing should be enabled by default when `mlflow` is installed, not gated behind a feature flag. The MLflow UI becomes Cognition's default trace viewer. This is the Payload-equivalent of "the admin UI comes for free."

### 6. Postgres is the production database

Support SQLite for local development (zero config) and Postgres for everything else. Don't build DynamoDB, MongoDB, or other backends. Be opinionated where it matters.

### 7. Docker is the production sandbox

Support local subprocess for development and Docker for production. Don't build K8s/Lambda backends until Docker is solid. The cloud backends are Phase 2.