# Changelog

All notable changes to Cognition are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.1]: https://github.com/CognicellAI/Cognition/releases/tag/v0.1.1
[0.1.0]: https://github.com/CognicellAI/Cognition/releases/tag/v0.1.0
