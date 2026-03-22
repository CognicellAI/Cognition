# Changelog

All notable changes to Cognition are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

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

[0.5.0]: https://github.com/CognicellAI/Cognition/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/CognicellAI/Cognition/releases/tag/v0.4.0
[0.1.1]: https://github.com/CognicellAI/Cognition/releases/tag/v0.1.1
[0.1.0]: https://github.com/CognicellAI/Cognition/releases/tag/v0.1.0
