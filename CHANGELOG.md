# Changelog

All notable changes to Cognition are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.8.2] — 2026-04-16

### Highlights

- Stabilized provider/model resolution, PostgreSQL startup, and delegated OpenAI-compatible model wiring for the 0.8.x line.

### Bug Fixes

- Fixed PostgreSQL-backed agent startup and streaming initialization so production deployments no longer fail during checkpointer and store setup.
- Fixed async SQLAlchemy/PostgreSQL DSN normalization so Cognition uses the correct driver format for SQLAlchemy and `asyncpg`.
- Fixed provider bootstrap from workspace config and OpenTelemetry startup so startup wiring matches configured deployment behavior.
- Fixed provider and session model configuration flow so unambiguous model-only session updates resolve correctly and invalid configurations fail explicitly.
- Fixed OpenAI-compatible subagent model conversion so delegated agents inherit compatible model settings correctly.

### Documentation and Tests

- Updated API and configuration documentation to reflect the corrected provider resolution and session configuration behavior.
- Added and expanded unit and API coverage for provider CRUD validation, session config normalization, REST health endpoints, and PostgreSQL DSN handling.

### Merged PRs

- `#101` fix postgres-backed agent startup and streaming
- `#102` fix provider bootstrap and otel startup
- `#106` fix: normalize postgres DSNs for async SQLAlchemy
- `#107` fix provider and model configuration flow
- `fix/openai-compatible-subagent-model` fix openai-compatible subagent model conversion

## [0.8.1] — 2026-04-13

### Highlights

- Captured the post-RFC cleanup train in release metadata and documentation after the architecture-deepening work landed.

### Documentation

- Updated release metadata and roadmap references to match the completed post-RFC cleanup train.
- Published the follow-up architectural cleanup record covering scoping, config store simplification, runtime cleanup, and storage deduplication.

### Merged PRs

- `#98` chore: update release metadata for v0.8.0
- `#99` chore: centralize version source
- `#100` docs: update roadmap for post-rfc cleanup train

## [0.8.0] — 2026-04-13

### Highlights

- Completed the architecture-deepening cleanup train, substantially simplifying Cognition's configuration, runtime, and storage architecture.

### Architecture and Runtime

- Completed the architecture-deepening RFC and follow-up cleanup train.
- Unified scoping across API routes.
- Removed dead `ConfigRegistry` globals.
- Collapsed agent definition management into `DefaultConfigStore`.
- Removed the `AgentRegistry` runtime.
- Simplified runtime resolution and removed deprecated streaming shims.
- Deduplicated shared storage backend logic across memory, SQLite, and PostgreSQL backends.

### Dependencies

- Refreshed core runtime and framework dependencies, including Deep Agents, FastAPI, Starlette, LangGraph, LangChain, OpenAI, Typer, Rich, Uvicorn, and WebSockets.
- Updated the lockfile and aligned runtime/test seams with the refreshed dependency set.

### Documentation and Examples

- Added a new `examples/` directory.
- Added an exhaustive `.cognition` reference example.
- Added focused examples for minimal, Bedrock, and scoped multi-tenant setups.
- Added sample API payloads for API-managed configuration entities.

### Merged PRs

- `#85` Complete architecture-deepening RFC and refresh core dependencies
- `#86` refactor: unify scoping API across all routes
- `#87` refactor: remove dead config registry globals
- `#88` refactor: collapse agent definition registry into config store
- `#89` refactor: remove agent registry runtime
- `#90` refactor: simplify runtime resolution and remove streaming shims
- `#91` refactor: deduplicate shared storage backend logic and add config examples
- `#97` docs: add exhaustive configuration examples

### Deferred Follow-up RFCs

Remaining optional architectural follow-ups were captured as issues:

- `#92` extract message send/resume workflow from API routes
- `#93` extract runtime execution plan from `DeepAgentStreamingService`
- `#94` separate unit and e2e fixture wiring
- `#95` remove legacy provider tuple compatibility from `RuntimeResolver`
- `#96` replace `api.dependencies` globals with a single DI strategy

## [0.7.0] — 2026-04-11

### Features

- **Kubernetes sandbox backend**: Cognition can now run agent commands in K8s-native sandboxes using the [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) CRD and controller. This is the third sandbox backend (alongside `local` and `docker`) and the only one that works when Cognition itself is deployed on Kubernetes with security hardening (`readOnlyRootFilesystem`, `capabilities.drop: ["ALL"]`, `runAsNonRoot`).

  The implementation follows the `langchain-<provider>` convention with a standalone `langchain-k8s-sandbox` workspace package that has zero Cognition imports. Cognition wraps it in `CognitionKubernetesSandboxBackend` which adds domain policy (protected paths, scoping labels, session lifecycle).

  Key capabilities:
  - Lazy sandbox creation on first `execute()` (no pod until agent invokes a shell tool)
  - `sh -c` command wrapping for heredoc/pipe support required by BaseSandbox file operations
  - TTL-based auto-cleanup via `spec.shutdownTime` patch on Sandbox CR
  - Scoping labels (`cognition.io/user`, `cognition.io/session`) on SandboxClaim CRs
  - Session deletion triggers `terminate()` via `SessionAgentManager.unregister_session()`
  - Startup validation checks CRD existence (fatal) and router health (warning)
  - Helm chart RBAC (namespace-scoped Role + cluster-scoped ClusterRole for CRD reads)
  - Optional NetworkPolicy to deny sandbox egress (`config.sandbox.k8s.denyEgress`)
  - SandboxTemplate example with `/tmp` and `/workspace` emptyDir volumes
  - 35 unit tests + 13 e2e tests (skip unless `COGNITION_K8S_E2E=1`)

### Bug Fixes

- Fix agent cache returning raw `CompiledStateGraph` instead of `CognitionAgentResult` on cache hit, causing `AttributeError: 'CompiledStateGraph' object has no attribute 'sandbox_backend'` in streaming service.

---

## [0.6.0] — 2026-03-23

### Features

- **Human-in-the-loop (HITL) tool approval end-to-end**: Cognition now surfaces native Deep Agents `interrupt_on` behavior through the API and SSE layers. Protected tool calls emit an `interrupt` event containing the tool name, arguments, action requests, and review config. Sessions move to `waiting_for_approval`, and `POST /sessions/{id}/resume` resumes execution using LangGraph `Command(resume=...)`. Approve, edit, and reject flows are covered with docker-compose E2E scenarios and a manual verification script.

- **Rich resume streaming for paused runs**: Resumed HITL sessions now stream live continuation events instead of returning a one-shot final result. After approval, resumed runs emit the same runtime event types as normal execution, including status, token, tool, planning, usage, and `done`.

- **Planning and `step_complete` SSE events from native Deep Agents todo state**: Cognition now translates Deep Agents/LangGraph `write_todos` state updates into first-class `planning` and `step_complete` SSE events. Clients can show plan creation and multi-step progress without parsing model text.

- **Structured output via Deep Agents `response_format`**: `AgentDefinition` and `SessionConfig` now support `response_format`, including dotted-path resolution to Pydantic model classes. Structured-output schemas are forwarded directly to `create_deep_agent(...)` instead of applying custom post-hoc validation in Cognition.

- **Deep Agents context controls exposed**: Cognition now wires through `tool_token_limit_before_evict` and declarative `summarization_tool` middleware support so builders can use upstream Deep Agents context/offloading controls from Cognition config.

- **Async message completion callbacks**: `POST /sessions/{id}/messages` now accepts `callback_url`. When a run finishes, Cognition sends a best-effort async `POST` containing session ID, message ID, status, final output, token usage, model used, and completion timestamp. This lets orchestration backends receive completion notifications without holding the SSE connection open.

- **Session metadata and filtering for orchestration APIs**: Sessions now support arbitrary builder-defined key-value metadata. `GET /sessions` supports filtering by metadata using query params such as `metadata.repository=myorg/myrepo` and `metadata.pr_number=42`, enabling external workflow reconciliation and lookup.

- **Message projection rebuild path from checkpoint state**: Cognition now formalizes the persistence contract that LangGraph checkpoint state is authoritative while the `messages` table is a read-optimized API projection. Storage backends can rebuild that projection from checkpoint messages, improving recovery after interrupted writes or projection drift.

### Enhancements

- **Agent and tool interrupt metadata exposed through API surfaces**: Agent responses now expose `interrupt_on` configuration, and tool CRUD supports `interrupt_on` metadata so builders can inspect or annotate HITL-related configuration more directly.

- **Message persistence contract documented**: Session/message documentation now explicitly explains the distinction between authoritative checkpoint state and the read-optimized message projection, including rebuild semantics.

- **Manual HITL verification workflow**: Added `scripts/manual_hitl_check.py` to verify interrupt -> `waiting_for_approval` -> resume -> completion behavior against a live server or docker-compose environment.

### Bug Fixes

- **Provider retry/timeout config now enforced**: `ProviderConfig.max_retries` and `ProviderConfig.timeout` are now forwarded into `init_chat_model(...)` and related provider construction paths, eliminating previously vestigial provider settings.

- **Stateless resume no longer depends on an in-memory runtime surviving across HTTP requests**: Resume now rebuilds the agent from persisted session/checkpoint state, matching LangGraph's interrupt/resume model and avoiding `409` failures caused by runtime cleanup after the original stream ended.

- **Interrupt extraction fixed for real Deep Agents runtime behavior**: HITL pauses are now detected from the `__interrupt__` update channel used in live Deep Agents execution, rather than relying only on exception-style interrupt handling.

- **Removed vestigial `store` parameter from `create_cognition_agent`**: The old `store` parameter was accepted, used in cache identity, and passed around by callers, but not meaningfully wired in the current agent creation path. It has now been removed to eliminate dead configuration and misleading call sites.

### Testing

- Added docker-compose E2E coverage for:
  - HITL approve flow
  - HITL edit flow
  - HITL reject flow
  - session metadata create/get/filter flows

- Added unit coverage for:
  - structured output wiring
  - context control wiring
  - resume command payloads
  - callback URL acceptance and dispatch
  - provider resolution tuple changes
  - message projection rebuild behavior
  - tool CRUD `interrupt_on` metadata
  - built-in agent registry expectations after adding `hitl_test`

### Documentation

- Updated session/message docs with:
  - session metadata examples
  - metadata filtering examples
  - callback URL behavior and payload shape
  - explicit projection-vs-checkpoint persistence model

### Deferred / Not Included

- **Per-session sandbox environment variables (#55)**: Investigated and partially prototyped, but intentionally not shipped. The write-only/in-memory API model worked, but the full Deep Agents execute-tool runtime path did not reliably receive injected env vars end-to-end. The issue remains open and documented for future work.
- **Context compaction observability (#52)**: Core Deep Agents wiring is present, but stronger external observability and deterministic verification remain open as follow-up work.

## [0.5.0] — 2026-03-22

### Features

- **Source-in-DB tools** (`POST /tools` with `code` field): Register custom Python tools via the REST API without filesystem access. Tool source is stored in the ConfigRegistry and executed at runtime. Enables builder applications running in separate containers to extend agent capabilities dynamically — no server restart, no container rebuild, no SSH required. `GET /tools` now returns tools from both file discovery and ConfigRegistry with a `source_type` discriminator (`file`, `api_code`, `api_path`).

- **LangGraph Store for cross-session agent memory**: All storage backends (`SqliteStorageBackend`, `PostgresStorageBackend`, `MemoryStorageBackend`) now expose `get_store()` returning a LangGraph `BaseStore` instance. `create_deep_agent()` receives `store=` and `context_schema=CognitionContext`, making `runtime.store` available inside agent nodes and middleware. `CognitionContext` scopes Store namespaces per user — user A cannot read user B's stored data. Built-in memory tools are planned (#45).

- **Full `AgentDefinition` field wiring**: `stream_response()` previously consumed only 3 of 12+ `AgentDefinition` fields. Now wires `memory`, `interrupt_on`, `middleware` (resolved from declarative names), `tools` (merged with registry tools), and `config.model`/`config.provider`/`config.temperature`/`config.recursion_limit` as a new agent-level override tier. Resolution hierarchy: session → agent definition → ConfigRegistry → global default.

### Dependency Upgrades

- **deepagents 0.3.12 → 0.4.12**: Includes security fix for `CompositeBackend` path boundary enforcement (0.4.6), bug fix for full routed path in write/edit results (0.4.7), and `create_summarization_tool_middleware()` factory (0.4.8).
- **langgraph 1.0.8 → 1.1.3**, **langchain 1.2.9 → 1.2.13**: Enables `astream()` with `version="v2"` unified StreamPart format.
- **anthropic 0.79.0 → 0.86.0**, **langchain-anthropic 1.3.2 → 1.4.0**.

### Bug Fixes

- **Streaming: broken tool call ID correlation**: `DeepAgentRuntime` was using `id(data)` — a CPython memory address — as `tool_call_id`, making it impossible for clients to correlate `tool_call` and `tool_result` events. Fixed by migrating from `astream_events()` to `astream(stream_mode=["messages", "updates", "custom"], subgraphs=True, version="v2")`. Tool IDs now come from real LangGraph-assigned identifiers.

- **Streaming: no subagent visibility**: `astream_events()` did not surface subgraph execution. Subagent activity is now emitted as `DelegationEvent` via `chunk["ns"]` namespace detection.

- **`AsyncPostgresStore` initialization error**: `from_conn_string()` is an `@asynccontextmanager`, not a coroutine. Calling it directly produced `'_AsyncGeneratorContextManager' object has no attribute 'setup'`, causing every streaming response to fail in Postgres deployments. Fixed by creating a `psycopg.AsyncConnection` directly (same pattern as `AsyncPostgresSaver`). Discovered via E2E testing against docker-compose.

- **`execute()` signature mismatch**: deepagents 0.4.3 added `timeout: int | None = None` to `SandboxBackendProtocol.execute()`. Updated `CognitionLocalSandboxBackend` and `CognitionDockerSandboxBackend` to match. Also fixed a latent `AttributeError`: `self._timeout` did not exist; now uses `self._default_timeout` from the parent class.

### Removed

- **`ContextManager` / `context.py`**: Removed `ContextManager`, `FileRelevanceScorer`, and `ProjectIndex` (~305 lines). `ContextManager` shelled out to `find`/`stat` at session init time (N+1 subprocess calls), producing a stale static snapshot. Agents now discover project structure dynamically via their built-in `ls`/`glob`/`grep` filesystem tools.

- **AST security scanning**: Removed `BANNED_IMPORTS`, `BANNED_CALLS`, `BANNED_OS_CALLS`, `SecurityASTVisitor`, and `scan_for_security_violations()` from `agent_registry.py`. AST scanning was bypassable via reflection and inconsistent with API-registered source-in-DB tools (which could not be scanned). `COGNITION_TOOL_SECURITY` (`warn`/`strict`) is also removed. The consistent security model is: trust is established at the API authentication boundary; `COGNITION_BLOCKED_TOOLS` enforces per-name tool blocking at the middleware layer. See `AGENTS.md` for the full trust model.

- **Dead code**: Removed `_planning_mode`, `_plan_todos`, `_current_step_index`, `_completed_steps` tracking variables from `stream_response()` (assigned but never read). Removed duplicate `write_todos` prompt injections from `SYSTEM_PROMPT` and `_build_messages()` — Deep Agents' `TodoListMiddleware` handles this natively.

### Migration Notes

| Change | Migration |
|---|---|
| `COGNITION_TOOL_SECURITY` removed | Remove from your `.env`. Use `COGNITION_BLOCKED_TOOLS` for tool name blocking; enforce authorization on `POST /tools` at the Gateway layer. |
| `ToolRegistration.path` is now optional | Existing DB entries with a `path` value continue to work. New entries require exactly one of `path` or `code`. |
| `POST /tools` now requires `code` XOR `path` | Pass `"code": "..."` for inline source or `"path": "module.path"` for a module path — not both, not neither. |
| Streaming `tool_call_id` format changed | Old: `"{tool_name}_{cpython_id}"`. New: real LangGraph-assigned ID (e.g. `"tool_ls_abc123"`). Update any client code that parsed the old format. |

---

## [0.4.0] — 2026-03-19

### Features

- **MCP server wiring**: `COGNITION_MCP_SERVERS` env var / `mcp_servers` YAML key
  configures remote MCP servers. Entries are validated (HTTP/HTTPS only) at startup.
  Tools from MCP servers are now available to the agent in all execution paths.
- **Bedrock IAM role support**: Ambient credentials (instance profile, ECS task role,
  Lambda, IRSA) work without any key configuration. `COGNITION_BEDROCK_ROLE_ARN`
  enables cross-account role assumption. `AWS_SESSION_TOKEN` supported for STS
  temporary credentials.
- **Configurable `recursion_limit` and `max_tokens` fully wired**: Both settings are
  now enforced end-to-end — globally via `COGNITION_AGENT_RECURSION_LIMIT` /
  `COGNITION_LLM_MAX_TOKENS`, per-agent via `AgentDefinition.config`, and
  per-session via `SessionConfig` (including storage round-trip in all backends).
- **`agent.recursion_limit`** added to `config.example.yaml`.

### Bug Fixes

- **Dead settings removed**: 11 unenforced fields (`ollama_model`, `ollama_base_url`,
  `max_sessions`, `session_timeout_seconds`, `llm_temperature`, `docker_timeout`,
  `protected_paths`, `prompt_source`, `prompt_fallback_to_local`, `prompts_dir`,
  `test_llm_mode`) removed from `Settings` to eliminate silent no-ops.
- **`.env` stale variables removed**: `LLM_PROVIDER`, `BEDROCK_MODEL_ID`,
  `USE_BEDROCK_IAM_ROLE`, `AGENT_BACKEND_ROUTES` were silently ignored due to wrong
  prefixes/names; removed from `.env.example`.
- **Missing `await` on `create_cognition_agent`**: Fixed in `runtime.py` and
  `session_manager.py` — coroutine was stored unresolved, silently breaking MCP and
  any execution path through those callers.
- **Rate limiter now enforced**: `COGNITION_RATE_LIMIT_PER_MINUTE` and
  `COGNITION_RATE_LIMIT_BURST` were reported in `/config` but ignored by the
  `RateLimiter`; now wired correctly in `main.py`.
- **Streaming content normalisation**: `DeepAgent streaming error: can only
  concatenate str (not "list") to str` — root cause was Bedrock Converse streaming
  deltas returning `list[dict]` without a `"type"` key. Fixed with a custom
  `_content_to_str()` in `runtime.py` that handles all three content formats (str,
  typed list, typeless Bedrock delta). `TokenEvent.__post_init__` added as a
  last-resort coercion barrier so the bug cannot recur from any future call site.
- **Bedrock partial-key validation**: Supplying only one of `AWS_ACCESS_KEY_ID` /
  `AWS_SECRET_ACCESS_KEY` now raises a clear `ValueError` at startup instead of
  silently producing a broken `ChatBedrock` instance.
- **`SessionConfig.recursion_limit` missing from storage backends**: All three
  backends (SQLite, Postgres, Memory) now serialize, merge, and deserialize
  `recursion_limit` correctly.

---

## [0.1.1] — 2026-03-06

### Performance

- Replaced QEMU-based multi-arch Docker builds with native parallel runners:
  `build-amd64` (`ubuntu-latest`) and `build-arm64` (`ubuntu-24.04-arm`) run
  simultaneously, then are merged into a single multi-arch manifest. Reduces
  release image build time from ~13 minutes to ~3–4 minutes.
- Switched Dockerfile builder stage from `pip install` to `uv sync --frozen`
  for deterministic, lockfile-driven dependency installation.
- Removed `test` extras (`pytest`, `pytest-cov`) from the production image.
- Added `warm-cache` CI job that pre-warms the GHA layer cache on every push
  to `main` so release builds always hit a warm cache.

---

## [0.1.0] — 2026-03-06

Initial public release of Cognition — a batteries-included AI backend built on
[DeepAgents](https://github.com/anomalyco/deepagents) and LangGraph.

### Features

**Agent Runtime**
- Multi-step ReAct loop via DeepAgents/LangGraph with automatic tool orchestration
- Configurable `recursion_limit` and `max_tokens` per agent
- Agent definitions via YAML or Markdown files in `.cognition/agents/`
- Built-in `default` and `readonly` agents; user definitions override built-ins by name
- Agent switching per session
- Subagent delegation support

**API & Streaming**
- FastAPI WebSocket + SSE streaming API
- REST endpoints: sessions, messages, agents, models, config
- `POST /sessions/{id}/messages` — stream agent responses as SSE events
- `DELETE /sessions/{id}/messages` (abort) — cancel in-flight agent runs
- `PATCH /config` / `POST /config/rollback` — live config management
- Multi-user scoping via namespace headers

**Tools**
- Built-in tools: file read/write/edit, bash execution, glob, grep
- Remote-only MCP (Model Context Protocol) support
- Tool registry with hot-reload via CLI (`cognition tools add/remove/reload`)
- Path traversal protection and workspace-root sandboxing

**LLM Providers**
- OpenAI (GPT-4o and compatible models)
- AWS Bedrock (Claude, Titan, etc.)
- OpenAI-compatible APIs (Ollama, vLLM, LiteLLM)
- Mock provider for testing

**Persistence**
- SQLite backend (default, zero-config)
- PostgreSQL backend (optional, for production multi-user deployments)
- LangGraph checkpoint integration for conversation history

**Observability**
- OpenTelemetry tracing (OTLP exporter)
- Prometheus metrics endpoint
- MLflow tracing integration (optional)
- Structured logging via structlog

**Execution & Sandboxing**
- Docker sandbox backend for isolated code execution
- Circuit breaker pattern for execution resilience
- Rate limiting per namespace

**CLI**
- `cognition` server CLI: `start`, `stop`, `status`, `tools`, `middleware`, `agents`
- `cognition-cli` TUI client for interactive sessions
- Session management: create, list, resume

**Configuration**
- `.cognition/config.yaml` — declarative agent/tool/middleware config
- Environment variable overrides via `.env`
- System prompt from inline text, file, or MLflow model registry

### CI/CD

- Strict `mypy` type checking across all production code (`server/`, `client/`)
- `ruff` linting with framework-aware rule suppression (FastAPI `Depends`, Typer `Option`)
- Automated Docker image builds on release:
  - `ghcr.io/cognicellai/cognition` — server image
  - `ghcr.io/cognicellai/cognition-sandbox` — sandbox execution image
  - Multi-arch: `linux/amd64` + `linux/arm64`
- PyPI publish on release (trusted publishing)

### Bug Fixes (pre-release)

- Fixed dead code (duplicate kwargs block) in `llm/registry.py`
- Fixed `StorageBackend` protocol — `create_message` missing `tool` role and `tool_call_id`
- Fixed `AgentDefinitionRegistry.list()` shadowing built-in `list` type; renamed to `get_all()`
- Fixed tool paths not resolving to `BaseTool` instances in `to_subagent()`
- Fixed SSE event parsing in e2e tests
- Fixed `DiscoveredModel` attribute access in models route
- Fixed stale `# type: ignore` comments after adding `types-PyYAML` stubs

[0.6.0]: https://github.com/CognicellAI/Cognition/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/CognicellAI/Cognition/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/CognicellAI/Cognition/releases/tag/v0.4.0
[0.1.1]: https://github.com/CognicellAI/Cognition/releases/tag/v0.1.1
[0.1.0]: https://github.com/CognicellAI/Cognition/releases/tag/v0.1.0
