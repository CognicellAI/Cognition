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
| **P0** (Table Stakes) | 6 | 6/6 | **100%** |
| **P1** (Production Ready) | 6 | 6/6 | **100%** |
| **P2** (Robustness) | 7 | 5/7 | **~85%** |
| **P3** (Full Vision) | 5 | 0/5 | **~30% partial** |

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

## P2 — Robustness (~85% Complete)

### P2-1: SSE Reconnection ✅

| Field | Value |
|-------|-------|
| **Layer** | 6 (API & Streaming) |
| **File** | `server/app/api/sse.py` |

Event IDs (`{counter}-{uuid}`), `retry:` directive (3000ms), keepalive heartbeat (15s), Last-Event-ID resume, circular event buffer. 38 unit tests.

### P2-2: Circuit Breaker 🔄 PARTIAL (Implemented, Not Wired)

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

## P3 — Full Vision (~30% Partial)

### P3-1: MLflow Evaluation Workflows 🔄 PARTIAL (40%)

| Field | Value |
|-------|-------|
| **Layer** | 7 (Observability) |
| **File** | `server/app/evaluation/workflows.py` |
| **Effort remaining** | ~2 weeks |

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

### P3-2: Prompt Registry 🔄 PARTIAL (60%)

| Field | Value |
|-------|-------|
| **Layer** | 7 (Observability) / 4 (Agent Runtime) |
| **File** | `server/app/agent/prompt_registry.py` |
| **Effort remaining** | ~3 days |

**What exists:**
- ✅ `PromptRegistryBackend` protocol
- ✅ `LocalPromptRegistry` — file-based, loads from `.cognition/prompts/`
- ✅ `MLflowPromptRegistry` — uses `mlflow.genai.load_prompt()` API
- ✅ `PromptRegistry` — unified with MLflow-first + local fallback, template formatting, lineage tracking

**What's missing:**
- [ ] Wire into `create_cognition_agent()` — currently the agent factory uses its own hardcoded `SYSTEM_PROMPT` and `settings.llm_system_prompt`, ignoring the registry
- [ ] Support registry references in `AgentDefinition` (e.g., `prompt: "mlflow:security-expert:v1"`)
- [ ] Integration tests with live MLflow server

### P3-3: Cloud Execution Backends

| Field | Value |
|-------|-------|
| **Layer** | 3 (Execution) |
| **Status** | Not started |
| **Effort** | ~2-4 weeks per backend |
| **Dependencies** | P2-6 (ExecutionBackend Protocol) ✅ |

**Acceptance Criteria:**
- [ ] At least one cloud backend (ECS or Lambda) implementing `ExecutionBackend`
- [ ] Container image registry integration
- [ ] Auto-scaling based on session demand
- [ ] Cost-aware scheduling
- [ ] Configuration-driven backend selection

### P3-4: Ollama Provider + LLM Resilience

| Field | Value |
|-------|-------|
| **Layer** | 5 (LLM Provider) |
| **Status** | Not started |
| **Effort** | ~1-2 weeks |
| **Dependencies** | P2-2 (Circuit Breaker) — partially done |

**Acceptance Criteria:**
- [ ] Ollama provider factory registered
- [ ] Streaming-level fallback: mid-stream provider failure triggers fallback
- [ ] Provider factories return typed protocol, not `Any`
- [ ] Gateway integration option (MLflow AI Gateway or LiteLLM)

### P3-5: Human Feedback Loop

| Field | Value |
|-------|-------|
| **Layer** | 7 (Observability) / 6 (API & Streaming) |
| **Status** | Not started |
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

---

## Next Steps

**Immediate (highest impact, lowest effort):**
1. Wire circuit breaker into `ProviderFallbackChain` (P2-2 completion — ~2 days)
2. Wire prompt registry into `create_cognition_agent()` (P3-2 completion — ~3 days)

**Short-term:**
3. Add API routes for evaluation service (P3-1 — ~1 week)
4. Persist evaluation feedback to database (P3-1 — ~3 days)

**Medium-term:**
5. Human feedback loop endpoint (P3-5 — ~1-2 weeks)
6. Ollama provider + LLM resilience (P3-4 — ~1-2 weeks)

**Long-term:**
7. Cloud execution backends (P3-3 — ~2-4 weeks per backend)

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
