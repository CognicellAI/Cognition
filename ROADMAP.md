# Cognition Roadmap

This roadmap is derived from [FIRST-PRINCIPLE-EVALUTION.md](./FIRST-PRINCIPLE-EVALUTION.md), the architectural source of truth for Cognition. All development must move the system toward the 7-layer architecture defined in that document.

**Governance:** This file must be updated before starting major work, merging architectural changes, or changing priority direction. See [AGENTS.md](./AGENTS.md) for enforcement rules.

**Priority rules:**
- P0 blocks P1. P1 blocks P2. P2 blocks P3.
- Security fixes override all priorities.
- Architecture corrections override feature work.

---

## Current State: ~55-60% Complete

The happy path works: create session, send message, stream SSE response with tool calls, session state survives restarts via LangGraph checkpoints. The configuration system, provider registry, fallback chain, SSE event taxonomy, and error hierarchy are solid foundations.

Critical gaps: messages are in-memory, `shell=True` is active, no session scoping, rate limiter is dead code, abort is a stub, Postgres silently falls back to SQLite, observability has no formal configuration.

---

## P0 -- Table Stakes

These issues must be resolved before any other work. They represent active security vulnerabilities, data loss, and governance failures.

### P0-1: Message Persistence

| Field | Value |
|---|---|
| **Layer** | 2 (Persistence) |
| **Status** | Not started |
| **Effort** | ~1 week |
| **Dependencies** | None |

**Problem:** Messages are stored in a module-global Python dict (`server/app/api/routes/messages.py:47`). All conversation history is lost on server restart.

**Acceptance Criteria:**
- [ ] Messages are persisted to SQLite (matching the existing session store pattern)
- [ ] Messages survive server restart
- [ ] Messages are scoped to their session
- [ ] Message retrieval supports pagination (existing API contract preserved)
- [ ] Existing `GET /sessions/{id}/messages` and `GET /sessions/{id}/messages/{mid}` endpoints work unchanged
- [ ] Unit tests cover message CRUD operations
- [ ] E2E test verifies message persistence across server restart

---

### P0-2: Remove `shell=True`

| Field | Value |
|---|---|
| **Layer** | 3 (Execution) |
| **Status** | Not started |
| **Effort** | ~1 day |
| **Dependencies** | None |

**Problem:** `server/app/sandbox.py:64` uses `subprocess.run(command, shell=True, ...)`. This enables shell injection attacks. AGENTS.md explicitly prohibits `shell=True`.

**Acceptance Criteria:**
- [ ] `shell=True` removed from `sandbox.py`
- [ ] Commands are parsed into argument lists (e.g., `shlex.split()`)
- [ ] Existing sandbox tests pass with argument list execution
- [ ] New test verifies shell metacharacters are not interpreted
- [ ] E2E sandbox workflow tests pass

---

### P0-3: Session Scoping Harness

| Field | Value |
|---|---|
| **Layer** | 6 (API & Streaming) |
| **Status** | Not started |
| **Effort** | ~1 week |
| **Dependencies** | None |

**Problem:** Zero session scoping exists. All sessions are visible to all callers. No per-user, per-project, or per-tenant isolation. The scoping mechanism must be generic -- downstream applications need to group sessions by different dimensions (user, project, team, etc.), not just user ID.

**Design:** Generic composable scoping via configurable scope headers. Sessions store scope key-value pairs as metadata. All session queries filter by provided scopes. Multiple scope dimensions can be active simultaneously (e.g., scope by both user and project).

**Acceptance Criteria:**
- [ ] Configurable `scope_keys` setting (list of required scope dimensions, e.g., `["user"]` or `["user", "project"]`)
- [ ] Scope values extracted from request headers using `X-Cognition-Scope-{Key}` convention (e.g., `X-Cognition-Scope-User`, `X-Cognition-Scope-Project`)
- [ ] Sessions store scopes as key-value metadata at creation time
- [ ] Session list, get, update, delete, and message endpoints filter by all provided scope values
- [ ] Fail-closed behavior when scoping is enabled: missing required scope headers returns 403
- [ ] Configuration toggle: `scoping_enabled` (default: `false` for backward compatibility)
- [ ] Unit tests for scope isolation (scope A cannot see scope B's sessions)
- [ ] Unit tests for multi-dimensional scoping (user + project combination)
- [ ] No authentication system built -- that belongs in the gateway (trusted proxy model)

---

### P0-4: Wire Rate Limiter

| Field | Value |
|---|---|
| **Layer** | 6 (API & Streaming) |
| **Status** | Not started |
| **Effort** | ~1 day |
| **Dependencies** | None |

**Problem:** `TokenBucket` rate limiter (`server/app/rate_limiter.py`) is started in the server lifespan but `check_rate_limit()` is never called from any endpoint or middleware.

**Acceptance Criteria:**
- [ ] Rate limiter middleware or dependency applied to message creation endpoint (`POST /sessions/{id}/messages`)
- [ ] Rate limit key is per-scope (when scoping enabled, e.g., per-user scope) or per-IP
- [ ] 429 response returned with `Retry-After` header when limit exceeded
- [ ] Rate limit configuration respected from settings (`rate_limit_rpm`, `rate_limit_burst`)
- [ ] Unit test for rate limiting behavior
- [ ] Existing rate limiter tests continue to pass

---

### P0-5: Functional Abort

| Field | Value |
|---|---|
| **Layer** | 4 (Agent Runtime) / 6 (API & Streaming) |
| **Status** | Not started |
| **Effort** | ~1 week |
| **Dependencies** | None |

**Problem:** `POST /sessions/{id}/abort` (`server/app/api/routes/sessions.py:252`) returns `{"success": True}` without cancelling anything. Comment: "In a real implementation..."

**Acceptance Criteria:**
- [ ] Abort endpoint cancels the active agent streaming task for the session
- [ ] SSE stream terminates with an appropriate error/done event on abort
- [ ] Session status updated to reflect cancellation
- [ ] Subsequent messages can be sent after abort (session is not permanently broken)
- [ ] Unit test for abort behavior
- [ ] E2E test: start streaming, abort, verify stream ends, send new message

---

### P0-6: Formalize Observability Configuration

| Field | Value |
|---|---|
| **Layer** | 7 (Observability) |
| **Status** | Not started |
| **Effort** | ~2 days |
| **Dependencies** | None |

**Problem:** OTel tracing is always-on with graceful degradation but has no explicit toggle. MLflow has a native Deep Agent integration via `mlflow.langchain.autolog()` but zero MLflow code exists in the server. There is no unified observability configuration that lets users choose their stack.

**Design:** Composable observability with two independent toggles: `otel_enabled` and `mlflow_enabled`. Users can run none, either, or both simultaneously. OTel covers operational metrics (request latency, error rates) and AI metrics (LLM call duration, tool call counts via spans). MLflow covers GenAI-specific depth (token tracking, reasoning chains, evaluation, prompt versioning). They serve complementary purposes and coexist cleanly since both are OTel-based.

| Configuration | What you get |
|---|---|
| Both disabled | Structured logging only |
| `otel_enabled=true` | Prometheus metrics + OTel spans to Jaeger (ops + AI metrics) |
| `mlflow_enabled=true` | MLflow autolog traces (deep GenAI tracing, evaluation-ready) |
| Both enabled | Full stack: operational monitoring + GenAI analysis |

**Acceptance Criteria:**
- [ ] `otel_enabled` setting added (default: `true`, preserving current behavior)
- [ ] `mlflow_enabled` setting added (default: `false`)
- [ ] Existing OTel/Prometheus setup gated behind `otel_enabled` toggle
- [ ] `mlflow[genai]` or `mlflow-tracing` added as optional dependency in `pyproject.toml`
- [ ] `mlflow_tracking_uri`, `mlflow_experiment_name` settings added
- [ ] `mlflow.langchain.autolog()` called during tracing setup when `mlflow_enabled=true`
- [ ] Graceful degradation: works without `mlflow` or `opentelemetry` packages installed
- [ ] When both disabled, only structured logging is active (no metrics, no traces)
- [ ] Config example and `.env.example` updated with all observability settings
- [ ] Unit test: verify OTel setup skipped when `otel_enabled=false`
- [ ] Unit test: verify MLflow setup skipped when `mlflow_enabled=false`

---

**P0 Total Estimated Effort: ~2-3 weeks**

---

## P1 -- Production Ready

These items are required for any production deployment. P0 must be complete before starting P1.

### P1-1: Unified StorageBackend Protocol

| Field | Value |
|---|---|
| **Layer** | 2 (Persistence) |
| **Status** | Not started |
| **Effort** | ~1 week |
| **Dependencies** | P0-1 (Message Persistence) |

**Problem:** Three separate persistence paths exist: `PersistenceBackend` (checkpoints only), `SqliteSessionStore` (concrete, non-pluggable), `_messages` dict. No single interface unifies sessions, messages, and checkpoints.

**Acceptance Criteria:**
- [ ] `StorageBackend` protocol defined with sub-interfaces: `SessionStore`, `MessageStore`, `Checkpointer`
- [ ] SQLite implementation of `StorageBackend` unifying existing session store and new message store
- [ ] Factory updated to create unified backend
- [ ] Connection pooling for SQLite (replace per-operation `aiosqlite.connect()`)
- [ ] Existing session store functionality preserved
- [ ] All existing tests pass against unified backend

---

### P1-2: Postgres Support

| Field | Value |
|---|---|
| **Layer** | 2 (Persistence) |
| **Status** | Not started |
| **Effort** | ~2 weeks |
| **Dependencies** | P1-1 (Unified StorageBackend) |

**Problem:** Settings accept `"postgres"` as `persistence_backend` but the factory silently falls back to SQLite. Zero Postgres code exists.

**Acceptance Criteria:**
- [ ] Postgres implementation of `StorageBackend` protocol
- [ ] Uses `asyncpg` or `sqlalchemy[asyncio]` with connection pooling
- [ ] Factory correctly dispatches to Postgres when configured
- [ ] Factory raises an error (not silent fallback) for unknown backend types
- [ ] Docker Compose updated with Postgres service for development
- [ ] Integration tests against Postgres (can use testcontainers or docker-compose)
- [ ] Settings validated: Postgres requires `database_url` to be set

---

### P1-3: Alembic Migrations

| Field | Value |
|---|---|
| **Layer** | 2 (Persistence) |
| **Status** | Not started |
| **Effort** | ~3 days |
| **Dependencies** | P1-1 (Unified StorageBackend) |

**Problem:** Schema is `CREATE TABLE IF NOT EXISTS` inline SQL. Schema evolution is impossible without manual intervention.

**Acceptance Criteria:**
- [ ] Alembic configured with async support
- [ ] Initial migration capturing current schema (sessions table + messages table)
- [ ] `cognition db upgrade` CLI command added
- [ ] `cognition db migrate` CLI command for generating new migrations
- [ ] Auto-migration on startup option (for development only)
- [ ] Works with both SQLite and Postgres backends

---

### P1-4: Docker Per-Session Sandbox

| Field | Value |
|---|---|
| **Layer** | 3 (Execution) |
| **Status** | Not started |
| **Effort** | ~2-3 weeks |
| **Dependencies** | P0-2 (Remove shell=True) |

**Problem:** The only execution path is local subprocess on the server host. No container isolation, no network isolation, no resource limits.

**Acceptance Criteria:**
- [ ] `DockerSandboxBackend` implementing the sandbox protocol
- [ ] Container-per-session lifecycle: create on session start, destroy on session end/timeout
- [ ] Workspace directory mounted as volume
- [ ] Optional network isolation (configurable)
- [ ] Resource limits: CPU, memory, disk (configurable)
- [ ] Output streaming from container
- [ ] Timeout enforcement at container level
- [ ] Fallback to local sandbox when Docker is unavailable (development mode)
- [ ] Configuration: `sandbox_backend = "local" | "docker"`
- [ ] Dockerfile for per-session sandbox container (separate from server Dockerfile)
- [ ] Integration tests with Docker

---

### P1-5: Declarative AgentDefinition

| Field | Value |
|---|---|
| **Layer** | 1 (Foundation) / 4 (Agent Runtime) |
| **Status** | Not started |
| **Effort** | ~1 week |
| **Dependencies** | None |

**Problem:** Agent creation is imperative kwargs to `create_cognition_agent()`. No validated schema captures an agent's full definition. This is the "define your agent, get everything" story.

**Acceptance Criteria:**
- [ ] `AgentDefinition` Pydantic V2 model in `server/app/models.py`
- [ ] Fields: tools, system_prompt, skills, middleware, subagents, interrupt_on, memory config
- [ ] `create_cognition_agent()` accepts `AgentDefinition` (in addition to kwargs for backward compat)
- [ ] `AgentDefinition` can be loaded from YAML (`.cognition/agent.yaml`)
- [ ] Validation: tools must be importable, skills must be valid paths
- [ ] Unit tests for model validation

---

### P1-6: AgentRuntime Protocol

| Field | Value |
|---|---|
| **Layer** | 4 (Agent Runtime) |
| **Status** | Not started |
| **Effort** | ~1 week |
| **Dependencies** | P1-5 (AgentDefinition) |

**Problem:** `create_cognition_agent()` returns `Any`. No Cognition-owned interface for the agent runtime. Swapping frameworks requires rewriting the factory and streaming service.

**Acceptance Criteria:**
- [ ] `AgentRuntime` protocol defined with methods: `astream_events()`, `ainvoke()`, `get_state()`, `abort()`
- [ ] Deep Agents wrapped in `AgentRuntime` protocol
- [ ] `DeepAgentStreamingService` programs against `AgentRuntime`, not deepagents internals
- [ ] Factory returns `AgentRuntime`, not `Any`
- [ ] Unit tests verify protocol compliance

---

**P1 Total Estimated Effort: ~6-8 weeks**

---

## P2 -- Robustness

Production hardening, resilience, and developer experience. P1 must be complete before starting P2.

### P2-1: SSE Reconnection

| Field | Value |
|---|---|
| **Layer** | 6 (API & Streaming) |
| **Status** | Not started |
| **Effort** | ~1 week |
| **Dependencies** | P0-1 (Message Persistence) |

**Acceptance Criteria:**
- [ ] SSE events include `id:` field for `Last-Event-ID` resumption
- [ ] `retry:` directive sent to clients
- [ ] Keepalive heartbeat events (`:` comment lines) at configurable interval
- [ ] `Last-Event-ID` header support: resume stream from where client disconnected
- [ ] Unit tests for event ID generation and resumption logic

---

### P2-2: Circuit Breaker and Retries

| Field | Value |
|---|---|
| **Layer** | 5 (LLM Provider) |
| **Status** | Not started |
| **Effort** | ~1-2 weeks |
| **Dependencies** | None |

**Problem:** Fallback chain docstring claims circuit breaker integration, but none exists. `max_retries` field on `ProviderConfig` is never read.

**Acceptance Criteria:**
- [ ] Circuit breaker per provider: open after N consecutive failures, half-open after timeout
- [ ] `max_retries` on `ProviderConfig` wired into fallback chain
- [ ] Exponential backoff between retries
- [ ] Circuit state exposed via health endpoint and Prometheus metric
- [ ] Proper token counting (replace `len(content.split())` with tiktoken or provider-reported)
- [ ] Unit tests for circuit breaker state transitions and retry behavior

---

### P2-3: Evaluation Pipeline Foundation

| Field | Value |
|---|---|
| **Layer** | 7 (Observability) |
| **Status** | Not started |
| **Effort** | ~2-3 weeks |
| **Dependencies** | P0-6 (Formalize Observability Configuration) |

**Problem:** Zero evaluation capability. This is the biggest capability gap in the platform.

**Acceptance Criteria:**
- [ ] MLflow evaluation integration (offline evaluation of agent traces)
- [ ] Built-in scorers: correctness, helpfulness, safety, tool efficiency
- [ ] Custom scorer registration API
- [ ] CLI command: `cognition eval` to run evaluation on recent sessions
- [ ] Evaluation results visible in MLflow UI
- [ ] Documentation for writing custom scorers

---

### P2-4: CORS Middleware

| Field | Value |
|---|---|
| **Layer** | 6 (API & Streaming) |
| **Status** | Not started |
| **Effort** | ~1 hour |
| **Dependencies** | None |

**Acceptance Criteria:**
- [ ] `CORSMiddleware` added to FastAPI app
- [ ] Configurable allowed origins, methods, headers via settings
- [ ] Default: permissive for development, restrictive for production

---

### P2-5: Enrich Message Model

| Field | Value |
|---|---|
| **Layer** | 1 (Foundation) |
| **Status** | Not started |
| **Effort** | ~2 days |
| **Dependencies** | P0-1 (Message Persistence), P1-3 (Alembic Migrations) |

**Acceptance Criteria:**
- [ ] `Message` model enriched: tool_calls, tool_call_id, token_count, model_used, metadata
- [ ] Migration for schema changes
- [ ] Streaming service populates enriched fields during message creation
- [ ] API models updated to expose enriched fields

---

### P2-6: Cognition-Owned Sandbox Protocol

| Field | Value |
|---|---|
| **Layer** | 3 (Execution) |
| **Status** | Not started |
| **Effort** | ~1 week |
| **Dependencies** | P1-4 (Docker Sandbox) |

**Problem:** Sandbox interface is `deepagents.SandboxBackendProtocol`, not Cognition-owned. Adding backends requires implementing a deepagents protocol.

**Acceptance Criteria:**
- [ ] `ExecutionBackend` protocol defined by Cognition with: `execute()`, `read_file()`, `write_file()`, `list_files()`, lifecycle methods
- [ ] Adapter from `ExecutionBackend` to `deepagents.SandboxBackendProtocol`
- [ ] Local and Docker backends implement `ExecutionBackend`
- [ ] Factory dispatches based on configuration

---

### P2-7: Integrate ContextManager

| Field | Value |
|---|---|
| **Layer** | 4 (Agent Runtime) |
| **Status** | Not started |
| **Effort** | ~3 days |
| **Dependencies** | None |

**Problem:** `ContextManager` (`server/app/agent/context.py`) is exported but never called by the agent factory or streaming service.

**Acceptance Criteria:**
- [ ] `ContextManager` wired into agent creation pipeline
- [ ] Project index built on session creation
- [ ] Relevant file context injected into system prompt or agent state
- [ ] Performance: context building does not add >500ms to session creation

---

**P2 Total Estimated Effort: ~4-5 weeks**

---

## P3 -- Full Vision

The complete "batteries-included" platform. P2 must be complete before starting P3.

### P3-1: MLflow Evaluation Workflows

| Field | Value |
|---|---|
| **Layer** | 7 (Observability) |
| **Status** | Not started |
| **Effort** | ~2-3 weeks |
| **Dependencies** | P2-3 (Evaluation Pipeline Foundation) |

See [MLFLOW-INTEROPERABILITY.md](./MLFLOW-INTEROPERABILITY.md) Stages 4-7.

**Acceptance Criteria:**
- [ ] Session-level experiment tracking (session -> MLflow run mapping)
- [ ] Custom scorers for agent-specific quality (tool efficiency, safety compliance)
- [ ] Human feedback loop: API endpoint to attach user feedback to traces
- [ ] Feedback-annotated traces used as evaluation datasets
- [ ] Quality trend dashboards in MLflow UI

---

### P3-2: Prompt Registry

| Field | Value |
|---|---|
| **Layer** | 7 (Observability) / 4 (Agent Runtime) |
| **Status** | Not started |
| **Effort** | ~1 week |
| **Dependencies** | P2-3 (Evaluation Pipeline) |

See [MLFLOW-INTEROPERABILITY.md](./MLFLOW-INTEROPERABILITY.md) Stage 6.

**Acceptance Criteria:**
- [ ] System prompts loaded from MLflow Prompt Registry when configured
- [ ] Version tracking and rollback support
- [ ] Lineage: prompt version linked to traces and evaluation scores
- [ ] Fallback to local prompt when MLflow unavailable

---

### P3-3: Cloud Execution Backends

| Field | Value |
|---|---|
| **Layer** | 3 (Execution) |
| **Status** | Not started |
| **Effort** | ~2-4 weeks per backend |
| **Dependencies** | P2-6 (Cognition Sandbox Protocol) |

**Acceptance Criteria:**
- [ ] At least one cloud backend (ECS or Lambda) implementing `ExecutionBackend`
- [ ] Container image registry integration
- [ ] Auto-scaling based on session demand
- [ ] Cost-aware scheduling
- [ ] Configuration-driven backend selection

---

### P3-4: Ollama Provider + LLM Resilience

| Field | Value |
|---|---|
| **Layer** | 5 (LLM Provider) |
| **Status** | Not started |
| **Effort** | ~1-2 weeks |
| **Dependencies** | P2-2 (Circuit Breaker) |

**Problem:** Settings define `ollama_model` and `ollama_base_url` but no factory is registered.

**Acceptance Criteria:**
- [ ] Ollama provider factory registered
- [ ] Streaming-level fallback: mid-stream provider failure triggers fallback
- [ ] Provider factories return typed protocol, not `Any`
- [ ] Gateway integration option (MLflow AI Gateway or LiteLLM)

---

### P3-5: Human Feedback Loop

| Field | Value |
|---|---|
| **Layer** | 7 (Observability) / 6 (API & Streaming) |
| **Status** | Not started |
| **Effort** | ~1-2 weeks |
| **Dependencies** | P3-1 (MLflow Evaluation Workflows) |

See [MLFLOW-INTEROPERABILITY.md](./MLFLOW-INTEROPERABILITY.md) Stage 7.

**Acceptance Criteria:**
- [ ] `POST /sessions/{id}/feedback` endpoint
- [ ] Feedback attached to MLflow traces
- [ ] Feedback-annotated traces become evaluation datasets
- [ ] Feedback-based filtering in MLflow UI

---

**P3 Total Estimated Effort: ~10-16 weeks**

---

## Summary

| Priority | Tasks | Estimated Effort | Cumulative |
|---|---|---|---|
| **P0** (Table Stakes) | 6 tasks | 2-3 weeks | 2-3 weeks |
| **P1** (Production Ready) | 6 tasks | 6-8 weeks | 8-11 weeks |
| **P2** (Robustness) | 7 tasks | 4-5 weeks | 12-16 weeks |
| **P3** (Full Vision) | 5 tasks | 10-16 weeks | 22-32 weeks |

**Total to reach "batteries included" parity: ~5-8 months of focused engineering.**

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
