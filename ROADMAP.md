# Cognition Production MVP Roadmap

This roadmap outlines the phases required to bring Cognition from its current prototype state to a production-ready Minimum Viable Product (MVP). Each phase builds upon the previous, with clear deliverables and success criteria.

---

## Phase 1: Core Foundation ✅

**Status**: Complete

### Deliverables
- ✅ Client-server architecture with WebSocket streaming
- ✅ In-process agent execution via LocalSandbox
- ✅ Basic message protocol (JSON over WebSocket)
- ✅ Simple Textual TUI for interaction
- ✅ Session management with automatic cleanup
- ✅ Support for multiple LLM providers (OpenAI, Bedrock, OpenAI-compatible)
- ✅ Project/workspace isolation

### Success Criteria
- Server starts and accepts WebSocket connections
- TUI client can connect, create projects, and chat with agent
- Agent can execute bash commands and file operations
- Messages stream in real-time from agent to client
- Sessions persist across disconnections (via checkpointing)

---

## Phase 2: Production Hardening ✅

**Status**: Complete

**Focus**: Reliability, observability, and robustness

### Deliverables

#### 2.1 Error Handling & Resilience ✅
- ✅ Comprehensive error handling in all async paths
- ✅ Custom exception hierarchy (`CognitionError`, `SessionError`, `LLMError`, etc.)
- ✅ Graceful degradation when LLM API fails
- ✅ Automatic retry logic with exponential backoff (3 attempts)
- ✅ Circuit breaker pattern for external API calls
- ✅ Structured error responses with error codes

#### 2.2 Observability ✅
- ✅ Structured logging (structlog) throughout codebase
- ✅ OpenTelemetry tracing for all major operations (with graceful fallback)
- ✅ Metrics collection (Prometheus-style)
  - LLM API latency (`llm_call_duration_seconds`)
  - Session count (`sessions_total`)
  - Tool call count (`tool_calls_total`)
  - Token usage per session
- ✅ Request/response logging with PII redaction

#### 2.3 Security ✅
- ✅ Input validation and sanitization
- ✅ Path traversal protection
- ✅ API key management with `SecretStr`
- ✅ Rate limiting per client (token bucket)
- ✅ Security headers middleware

#### 2.4 Testing Infrastructure ✅
- ✅ Unit tests for all major components
- ✅ Integration tests for agent execution
- ✅ E2E tests for full user flows
- ✅ Test fixtures and mocks

### Success Criteria
- 95%+ test coverage
- Zero unhandled exceptions in normal operation
- All operations are observable and debuggable
- Security audit passes
- Circuit breaker opens within 3 failures, recovers after 60s

---

## Phase 3: Dynamic Model Management ✅

**Status**: Complete

**Focus**: Multi-LLM support with model registry, usage tracking, and configuration

### Deliverables

#### 3.1 Model Registry (models.dev) ✅
- ✅ Integration with models.dev API
- ✅ Local caching of model metadata (1-hour TTL)
- ✅ Search and filter by provider, capability, cost
- ✅ Real-time pricing data
- ✅ 88+ providers supported

#### 3.2 Provider Fallback Chain ✅
- ✅ Ordered provider fallback (OpenAI → Bedrock → mock)
- ✅ Per-provider configuration (timeout, retry, etc.)
- ✅ Seamless switching on provider failure
- ✅ Health checks per provider

#### 3.3 Token Usage Tracking ✅
- ✅ Per-session token counting
- ✅ Cost estimation with real models.dev pricing
- ✅ Usage analytics
- ✅ Cost tracking per project

#### 3.4 Per-Session Model Configuration ✅
- ✅ Dynamic model switching mid-session
- ✅ Temperature, max_tokens customization
- ✅ System prompt per-session
- ✅ Configuration merging semantics

#### 3.5 Context Window Management ✅
- ✅ Token estimation (char-based)
- ✅ Context budget computation
- ✅ Smart message truncation
- ✅ Overflow protection

### Protocol Extensions ✅

| Direction | Type | Purpose |
|---|---|---|
| Client → Server | `configure_session` | Switch provider/model/temperature for a session |
| Client → Server | `init_agents_md` | Create AGENTS.md in project workspace |
| Server → Client | `session_configured` | Confirms session config update |
| Server → Client | `agents_md_created` | Confirms AGENTS.md creation |
| Server → Client | `usage_update` | Token usage + cost after each turn |

### New Modules

| Module | Lines | Purpose |
|---|---|---|
| `server/app/llm/model_registry.py` | ~330 | models.dev API client + cache |
| `server/app/llm/usage_tracker.py` | ~260 | Token/cost tracking per session |
| `server/app/llm/provider_fallback.py` | ~230 | Ordered provider fallback chain |
| `server/app/llm/model_config.py` | ~180 | Per-session model configuration |
| `server/app/llm/context_window.py` | ~280 | Token counting + message truncation |

### Tests (66 new tests)

| Test File | Tests | Coverage |
|---|---|---|
| `test_model_registry.py` | 27 | Registry, providers, search, filters, cache |
| `test_llm_phase3.py` | 39 | Context window, model config, usage tracker |

### Success Criteria ✅
- ✅ Seamless switching between providers via protocol message
- ✅ Token usage tracking with per-event cost estimation
- ✅ Context window management prevents overflow
- ✅ Cost estimates use real models.dev pricing data

---

## Phase 4: Advanced Agent Capabilities ✅ COMPLETE

**Status**: Complete (40/40 tests passing)

**Focus**: Making the agent more capable and context-aware

### Deliverables

#### 4.1 Enhanced Tool System ✅
- [x] Git integration (status, diff, log, branch)
- [x] Code search (grep, find)
- [x] Test runner integration (pytest, jest)
- [x] Linter integration (ruff, eslint, mypy)
- [ ] Web search capability (optional/opt-in)
- [ ] LSP integration for code intelligence

**Files**: `server/app/agent/tools.py` (~220 lines)

#### 4.2 Context Management ✅
- [x] Automatic project indexing
- [x] File relevance scoring
- [x] Smart file inclusion in context
- [x] Language detection from file extensions
- [ ] Long-term memory via StoreBackend
- [ ] Cross-session learning (preferences, patterns)

**Files**: `server/app/agent/context.py` (~280 lines)

#### 4.3 Agent Workflows ✅
- [x] Multi-step planning and execution
- [x] Task orchestration with dependencies
- [x] Change tracking (undo/redo)
- [x] Human-in-the-loop approvals
- [x] Progress reporting

**Files**: `server/app/agent/workflows.py` (~380 lines)

#### 4.4 Output Formatting ✅
- [x] Diff visualization (unified and compact)
- [x] Syntax highlighting for code
- [x] Tool call formatting
- [x] Collapsible sections
- [x] Output truncation for long content
- [ ] Rich markdown rendering

**Files**: `server/app/agent/output.py` (~300 lines)

### Implementation Notes
- Built on LangGraph Deep Agents foundation
- All features integrated into existing agent architecture
- 40 comprehensive unit tests covering all components

### Success Criteria ✅
- Agent can complete multi-step tasks autonomously
- Context includes only relevant files
- File changes are previewed before application
- 90%+ accuracy on file search queries
- All tests passing (211/213)

---

## Phase 5: REST API Migration & OpenAPI Documentation

**Status**: Complete (Server-side only, client deferred)

**Focus**: Replace WebSocket protocol with REST + Server-Sent Events (SSE) for improved observability, tooling, and documentation

### Rationale

The current WebSocket protocol has limitations:
- ❌ No OpenAPI support (custom binary protocol)
- ❌ Harder to debug without specialized tools
- ❌ No auto-generated SDKs
- ❌ No browser/proxy-friendly observability

REST + SSE provides:
- ✅ Full OpenAPI 3.1 documentation
- ✅ Auto-generated SDKs (TypeScript, Python, etc.)
- ✅ Standard HTTP debugging tools (curl, Postman, browser devtools)
- ✅ Native FastAPI support
- ✅ Load balancer and CDN compatibility
- ✅ Clear request/response semantics

### Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Client                           │
│                                                     │
│  POST /projects              ────────►              │
│  POST /sessions              ────────►              │
│  POST /sessions/:id/messages ────────► SSE stream   │
│                                 │    (streaming)    │
│                                 │                   │
│                                 ▼                   │
│                        ┌──────────────────────┐     │
│                        │   Token events       │     │
│                        │   Tool call events   │     │
│                        │   Tool result events │     │
│                        │   Done event         │     │
│                        └──────────────────────┘     │
└─────────────────────────────────────────────────────┘
```

### Deliverables

#### 5.1 REST API Design ✅
- [x] Define all REST endpoints with Pydantic request/response models
- [x] Document SSE event types and schemas
- [x] Design error response format
- [ ] Define authentication strategy (Phase 6)

**Endpoints:**

| Method | Path | Purpose | Response |
|--------|------|---------|----------|
| `POST` | `/projects` | Create new project | `Project` |
| `GET` | `/projects` | List all projects | `Project[]` |
| `GET` | `/projects/:id` | Get project details | `Project` |
| `POST` | `/sessions` | Create session | `Session` |
| `GET` | `/sessions` | List sessions | `Session[]` |
| `GET` | `/sessions/:id` | Get session details | `Session` |
| `DELETE` | `/sessions/:id` | Delete session | `boolean` |
| `POST` | `/sessions/:id/messages` | Send message | SSE stream |
| `POST` | `/sessions/:id/abort` | Abort current operation | `boolean` |
| `PATCH` | `/sessions/:id/config` | Update session config | `Session` |
| `GET` | `/health` | Health check | `HealthStatus` |
| `GET` | `/ready` | Readiness probe | `ReadyStatus` |

#### 5.2 Server Implementation ✅
- [x] Implement all REST endpoints in FastAPI
- [x] Implement SSE streaming for message responses
- [x] Add request/response validation
- [x] Add comprehensive logging
- [x] Maintain backward compatibility during transition

**Files:**
- `server/app/api/routes/projects.py`
- `server/app/api/routes/sessions.py`
- `server/app/api/routes/messages.py`
- `server/app/api/sse.py` (SSE utilities)
- `server/app/api/models.py` (Pydantic schemas)

#### 5.3 OpenAPI Documentation ✅
- [x] Auto-generated OpenAPI spec at `/docs`
- [x] Interactive Swagger UI
- [x] ReDoc alternative documentation
- [x] Tag and organize endpoints
- [x] Add request/response examples
- [x] Document SSE event stream format

**Files:**
- `server/app/main.py` (FastAPI OpenAPI config)
- `docs/openapi.yaml` (static export)

#### 5.4 Client Architecture (Deferred) ❌
- ❌ ~~Update TUI client to use REST + SSE~~
- ❌ ~~Implement SSE event parsing~~
- ❌ ~~Handle connection errors and reconnection~~

**Decision**: TUI client approach abandoned - too complex for MVP. Will use simpler CLI + web client approach in Phase 6.

#### 5.5 Configuration Management ✅
- [x] YAML configuration support (`~/.cognition/config.yaml`)
- [x] Project-level configuration (`.cognition/config.yaml`)
- [x] Configuration hierarchy (defaults → global → project → env)
- [x] Configuration validation
- [x] Typer CLI for server startup

**Configuration Schema:**
```yaml
# ~/.cognition/config.yaml (global)
server:
  host: 127.0.0.1
  port: 8000
  log_level: info

llm:
  provider: openai
  model: gpt-4o
  temperature: 0.7
  max_tokens: 4096

agent:
  system_prompt: "..."
  max_iterations: 15

workspace:
  root: ./workspaces

rate_limit:
  per_minute: 60
  burst: 10
```

#### 5.6 Documentation ✅
- [x] README with quick start
- [x] Configuration reference
- [x] Troubleshooting FAQ
- [x] One example project (Python)

**Deferred to Phase 6:**
- Interactive tutorial
- Video walkthroughs
- Multi-language examples
- Full API reference (OpenAPI covers this)

### Success Criteria ✅
- ✅ All endpoints documented in OpenAPI spec
- ✅ Message streaming works with SSE
- ✅ SDK can be auto-generated from OpenAPI spec
- ✅ Configuration loaded from YAML files
- ✅ Tests updated for new API
- ❌ ~~TUI client connects via REST + SSE~~ (deferred - will use simpler CLI)

---

## Phase 6: Production Readiness

**Status**: Not Started

**Focus**: Multi-user deployment, authentication, data persistence

### Deliverables

#### 6.1 Containerization ✅
- [x] Dockerfile for server (multi-stage, security-hardened)
- [x] Docker Compose setup (Cognition + full observability stack)
- [x] Volume management for workspaces
- [x] Environment-specific configurations (.env.example)
- [x] Health checks in containers

**Observability Stack:**
- **Grafana** (port 3000) - Pre-built Cognition dashboard
- **Prometheus** (port 9091) - Metrics collection
- **Jaeger** (port 16686) - Distributed tracing via OTLP (full agent internals)
- **Loki + Promtail** - Log aggregation from containers

**Quick Start:**
```bash
# Start everything
docker-compose up -d

# Or use Make
make docker-up

# Access:
# - Cognition API: http://localhost:8000
# - Grafana: http://localhost:3000 (admin/admin)
# - Jaeger: http://localhost:16686
# - Prometheus: http://localhost:9091
```

#### 6.2 Agent Execution Environment (Sandbox) ✅
- [x] **Local Sandbox Backend**: Hybrid native/shell isolation
  - Inherits from `FilesystemBackend` for robust, native file operations
  - Uses `LocalSandbox` for safe shell command execution via `subprocess`
  - Safe path resolution (prevents escaping workspace)
- [x] **Multi-step Task Completion**: Automatic ReAct loop via `deepagents`
  - Planning with `write_todos`
  - Tool chaining and error recovery
  - Automatic state persistence via `thread_id`
- [x] **Observability Integration**: Full tracing of agent internals via OpenTelemetry auto-instrumentation

**Files:**
- `server/app/agent/sandbox_backend.py`
- `server/app/agent/cognition_agent.py`
- `server/app/llm/deep_agent_service.py`
- `docs/sandbox/`

#### 6.3 Persistence & Data Management ✅
- [x] **Pluggable Persistence Layer**: Abstract `PersistenceBackend` interface
- [x] **SQLite Backend**: `SqliteBackend` using `AsyncSqliteSaver` for agent state
- [x] **Session Storage Migration**: Consolidated `LocalSessionStore` to SQLite (`.cognition/state.db`)
- [x] **Resilience**: Chat history and sessions survive server restarts
- [ ] **Database Migration Tool**: (Deferred until schema stabilizes)

**Files:**
- `server/app/persistence/`
- `server/app/session_store.py` (SQLite implementation)

#### 6.4 Client Architecture (CLI)
- [ ] Lightweight CLI client using `httpx` and `typer`
- [ ] Interactive chat mode
- [ ] Session management commands
- [ ] Output formatting (markdown, syntax highlighting)

#### 6.5 Multi-User Support (Deferred)
Moved to future Enterprise/Cloud phase.
- [ ] User authentication (API keys, JWT)
- [ ] Workspace isolation between users
- [ ] Resource quotas per user
- [ ] Server-initiated events via SSE (`GET /events`)
- [ ] Background task support
- [ ] Session sharing and collaboration
- [ ] Advanced TUI features (themes, keybinds, etc.)

### Success Criteria
- One-command deployment: `docker-compose up`
- Supports 10+ users on single instance
- Automatic SSL certificate management
- Zero-downtime updates
- Complete audit trail

---

## Phase 7: Enterprise & Scale (Post-MVP)

**Status**: Not Started

**Focus**: Advanced features for larger teams and distributed systems

### Deliverables

#### 7.1 Kubernetes Operator
- [ ] CognitionTenant CRD
- [ ] Operator controller
- [ ] Namespace isolation
- [ ] RBAC integration
- [ ] Network policies

#### 7.2 Multi-Tenancy
- [ ] Tenant onboarding/offboarding
- [ ] Resource quotas and limits
- [ ] Billing integration hooks
- [ ] SSO integration (SAML, OIDC)
- [ ] Admin dashboard

#### 7.3 Advanced Communication
- [ ] **Hybrid Architecture**: REST + SSE externally, gRPC internally
- [ ] Agent-to-agent gRPC communication
- [ ] Containerized agent execution
- [ ] Multi-region support

**Future Hybrid Architecture:**
```
┌──────────────┐     REST + SSE      ┌──────────────┐
│   Clients    │ ◄──────────────────► │   API Layer  │
│ (TUI/Web/IDE)│                      │   (FastAPI)  │
└──────────────┘                      └──────┬───────┘
                                             │
                                        gRPC │ (internal)
                                             │
                           ┌─────────────────┼─────────────────┐
                           │                 │                 │
                     ┌─────▼─────┐    ┌──────▼────┐    ┌──────▼────┐
                     │  Agent A  │    │  Agent B  │    │  Agent C  │
                     │ (planning)│    │ (coding)  │    │ (testing) │
                     └───────────┘    └───────────┘    └───────────┘
```

**When gRPC makes sense:**
- Agent-to-agent communication (Phase 7+)
- Containerized execution layer
- Multi-region deployment
- High-throughput tool execution

#### 7.4 Enterprise Security
- [ ] SOC 2 compliance features
- [ ] Data residency controls
- [ ] Audit logging to SIEM
- [ ] Secret rotation
- [ ] Penetration testing

#### 7.5 Scalability
- [ ] Horizontal pod autoscaling
- [ ] Database sharding
- [ ] Caching layer (Redis)
- [ ] CDN for static assets

### Success Criteria
- SOC 2 Type II compliant
- 1000+ concurrent users
- 99.9% uptime
- <100ms latency for token streaming
- Complete audit trail retention (1 year)

---

## Phase Dependencies

```
Phase 1 (Foundation)
    ↓
Phase 2 (Hardening) → Phase 3 (Multi-LLM)
    ↓                       ↓
    ↓                       ↓
Phase 4 (Capabilities) ←──┘
    ↓
Phase 5 (REST API)
    ↓
Phase 6 (Production)
    ↓
Phase 7 (Enterprise)
```

## Definition of MVP

**Production MVP** is achieved at the completion of Phase 6 with:

1. **Stability**: 7-day uptime, graceful error handling
2. **Security**: No secrets in logs, input validation, audit trails
3. **Observability**: Structured logging, metrics, tracing
4. **Multi-user**: 10+ users, isolation, quotas
5. **Multi-LLM**: OpenAI, Bedrock, local models
6. **Usability**: TUI + docs, <10 min onboarding
7. **Deployability**: Docker Compose, single command
8. **Testing**: >80% coverage, E2E tests pass

## Exit Criteria for Each Phase

Before moving to the next phase:

1. **All deliverables complete** (or consciously deferred)
2. **Tests pass** (unit, integration, E2E)
3. **Documentation updated**
4. **No critical bugs**
5. **Performance benchmarks met**
6. **Security review** (for phases 2+)

## Notes

- Phases can overlap where dependencies allow
- Some features may be cut to reach MVP faster
- Phase 7 is explicitly post-MVP (requires significant investment)
- Each phase includes "polish" items that improve UX
- REST + SSE chosen for Phase 5 for OpenAPI compatibility and tooling
- gRPC reserved for Phase 7 internal communication only

## Current Status

**Completed**: Phase 1 (Core Foundation) ✅, Phase 2 (Production Hardening) ✅, Phase 3 (Multi-LLM & Model Management) ✅, Phase 4 (Advanced Agent Capabilities) ✅, Phase 5 (REST API Server) ✅, Phase 6.1 (Containerization & Observability) ✅, Phase 6.2 (Sandbox Execution) ✅, Phase 6.3 (Persistence) ✅
**In Progress**: Phase 6.4 (CLI Client)
**Deferred**: Multi-User Support, TUI client

See GitHub issues for detailed task breakdown per phase.

### Phase 3 Summary
Phase 3 has been completed with the following achievements:
1. **Model Registry** - Full models.dev API integration with local caching (88+ providers)
2. **Provider Fallback** - Ordered fallback chain with per-provider configuration
3. **Token Usage Tracking** - Per-session and per-project tracking with cost estimation
4. **Per-Session Config** - Temperature, max_tokens, system prompt overrides via protocol
5. **Context Window Management** - Token estimation, budget computation, smart truncation
6. **Protocol Extensions** - 5 new message types for model switching, config, and usage
7. **Comprehensive Tests** - 66 new unit tests (total: 155+ passing)

### Phase 4 Summary
Phase 4 has been completed with the following achievements:
1. **Enhanced Tool System** - Git tools, search, test runners, linters
2. **Context Management** - Project indexing, file relevance scoring, smart inclusion
3. **Agent Workflows** - Multi-step planning, orchestration, approvals, undo/redo
4. **Output Formatting** - Diff visualization, syntax highlighting, tool call formatting
5. **Comprehensive Tests** - 40 new unit tests (total: 211 passing)

### Phase 5 Summary
Phase 5 server-side REST API migration completed with:
1. **REST API Design** - 11 endpoints with Pydantic models
2. **SSE Streaming** - Real-time message streaming via Server-Sent Events
3. **OpenAPI Documentation** - Auto-generated spec at `/docs`, Swagger UI
4. **Configuration System** - YAML config with hierarchical loading
5. **Comprehensive Tests** - E2E and unit tests for REST API

**Note**: TUI client was abandoned - too complex. Will use simpler CLI approach in Phase 6.

### Phase 6 Plans
Phase 6 focuses on Production Readiness, including containerization, advanced agent execution environments, and multi-user support.

**Completed so far (6.1 - 6.2):**
1. **Full Observability Stack**: Docker Compose with Jaeger, Prometheus, Grafana, Loki.
2. **Auto-Instrumentation**: Full internal tracing of LangChain/DeepAgents components.
3. **Local Sandbox Backend**: Robust hybrid backend combining native Python file I/O with isolated shell execution.
4. **Autonomous Execution**: Automatic multi-step ReAct loop with error recovery and planning.

**Next Steps (6.3+):**
- Implement user authentication and isolation.
- Develop the lightweight CLI client.
- Add database persistence (SQLite/Postgres).
