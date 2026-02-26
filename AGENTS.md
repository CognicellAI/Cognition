# AGENTS.md — Cognition Coding Agent

Guidelines for agentic coding assistants working on this codebase.

## Project Overview

Cognition is an OpenCode-style coding agent with:
- **Server**: FastAPI WebSocket API with Deep Agents runtime (`server/`)
- **Client**: CLI/TUI for interactive sessions (`client/`)
- **Execution**: Container-per-session with optional network isolation
- **Scope**: Python repos only, pytest-based testing

## Build / Test / Lint Commands

This project uses `uv` for dependency management and task execution.

```bash
# Install dependencies
uv sync

# Run server (development)
# Runs the FastAPI server with hot reload
uv run uvicorn server.app.main:app --reload --port 8000

# Run client (development)
# Starts the CLI/TUI client
uv run python -m client.cli.main

# Run all tests
uv run pytest

# Run single test file
uv run pytest tests/unit/test_settings.py -v

# Run single test case
uv run pytest tests/unit/test_settings.py::TestSettingsDefaults::test_default_server_settings -v

# Type checking (Strict)
uv run mypy .

# Linting & Formatting (Ruff)
uv run ruff check .
uv run ruff format .
```

## Code Style Guidelines

### Python Standards
- **Python 3.11+** required.
- **Type Hints**: strict `mypy` compliance. Use `from __future__ import annotations`.
- **Docstrings**: Google style for all public functions/classes.
- **Line Length**: Follow `ruff` config (default 88/100).
- **Imports**: Grouped as stdlib, third-party, local. Use absolute imports.

### Naming Conventions
- `snake_case`: functions, variables, modules.
- `PascalCase`: classes, types.
- `UPPER_CASE`: constants.
- `_prefix`: private methods/attributes.
- `async` functions: often prefixed with verbs like `get_`, `fetch_`, `handle_`.

### Async Patterns
- **Async/Await**: Use for all I/O operations (DB, Network, File).
- **Concurrency**: Use `asyncio.gather()` for parallel independent tasks.
- **Subprocesses**: Use `asyncio.create_subprocess_exec`.

### Error Handling
- **Exceptions**: Use custom hierarchy in `server/app/exceptions.py`.
- **Pattern**: Catch external errors -> Raise domain-specific `CognitionError`.
- **Logging**: Log errors before raising if context is needed, otherwise let middleware handle it.

### Data Models
- **Pydantic V2**: Use for all data structures and validation.
- **Settings**: Use `pydantic-settings` (e.g., `server/app/settings.py`).
- **Secrets**: Use `SecretStr` for sensitive data.

## Project Structure

```
cognition/
├── server/
│   └── app/
│       ├── agent/       # Deep Agents runtime & tools
│       ├── api/         # FastAPI routes
│       ├── llm/         # LLM service integration
│       └── persistence/ # Database/Storage
├── client/
│   └── cli/             # CLI/TUI entry points
├── tests/
│   ├── e2e/             # End-to-end full workflow tests
│   └── unit/            # Isolated unit tests
├── pyproject.toml       # Project dependencies & tool config
└── uv.lock              # Dependency lock file
```

## Key Workflows

### Extending Cognition
Cognition is designed to be highly pluggable using native `deepagents` extension points. Deep Agents is the higher-level abstraction that Cognition is built on — it wraps LangGraph and provides its own primitives (tools, middleware, skills, subagents, memory, sandbox backends). We should prefer Deep Agents' API surface over reaching down to raw LangGraph.

#### Custom Tools
1. Define your tool as a plain Python callable or LangChain `BaseTool`.
2. Register it in `.cognition/config.yaml` under `agent.tools` or pass it to `create_cognition_agent(tools=[...])`.

#### Agent Middleware
1. Implement `deepagents.middleware.AgentMiddleware` for lifecycle hooks (observability, status streaming, etc.).
2. Add to `create_cognition_agent(middleware=[...])`.

**Available upstream middleware** (declarative in `.cognition/agent.yaml`):
- `tool_retry` - Exponential backoff retry on tool failure
- `tool_call_limit` - Per-tool and global call limits
- `human_in_the_loop` - Approve/edit/reject tool calls before execution
- `pii` - Detect and redact PII (email, credit card, IP, etc.)

#### Skills & Memory
1. Add `SKILL.md` files to `.cognition/skills/` for progressive disclosure capabilities.
2. Update `AGENTS.md` in the workspace to provide the agent with project-specific conventions.

#### Subagents
1. Define specialized subagents in `.cognition/config.yaml` to handle complex domain-specific tasks in isolated contexts.

### Testing & Scenarios
- **Unit Tests**: Fast, mocked dependencies. No containers.
- **E2E Tests**: Use `tests/e2e/`. May require running server.
- **Mocking**: Use `unittest.mock` or `pytest-mock` for external services (LLMs).
- **Scenarios**: Scenarios are business logic use case against the APIs. They should be tested in E2E tests with realistic inputs against the docker-compose environment.
  - If keys are not available for external services, let the user know to set them in `.env`.

## Security & Safety
- **Path Traversal**: Validate all file paths against workspace root.
- **Command Execution**: No shell=True. Use argument lists.
- **Secrets**: Never commit keys. Use `.env` and `Settings` class.


# Hard Requirements


## Mission

Cognition is a **batteries-included AI backend**.

An agent definition (tools, prompt, skills, middleware) must be sufficient to generate:

* API
* Streaming
* Persistence
* Sandboxing
* Observability
* Multi-user scoping
* Evaluation

The FIRST-PRINCIPLE-EVALUTION.md document  is the architectural source of truth.

All development must move the system toward that architecture.

---

# 0. Mandatory Roadmap Governance

## ROADMAP.md Is Required

A `ROADMAP.md` file must exist at the repository root.

It must:

1. Reflect the priority structure defined in the First Principles Evaluation:

   * P0 (Table Stakes)
   * P1 (Production-Ready)
   * P2 (Robustness)
   * P3 (Full Vision)

2. Break each priority into:

   * Concrete tasks
   * Layer assignment (1–7)
   * Acceptance criteria
   * Estimated effort
   * Dependencies

3. Be updated before:

   * Starting major work
   * Merging architectural changes
   * Changing priority direction

---

## Agents Must Adhere to ROADMAP.md

Agents are not permitted to:

* Implement large features not listed in ROADMAP.md
* Skip P0 work to build P2/P3 features
* Add architectural scope creep without roadmap update
* Introduce new subsystems without a roadmap entry

If a change is not in ROADMAP.md:

* The agent must first update ROADMAP.md
* Justify priority placement
* Then proceed

Roadmap discipline is mandatory.

---

## Roadmap Precedence Rules

1. P0 blocks P1
2. P1 blocks P2
3. P2 blocks P3
4. Security fixes override all priorities
5. Architecture corrections override feature work

No “cool feature” work is allowed while:

* Messages are in-memory
* `shell=True` exists
* Multi-user isolation is missing
* Abort is a stub
* Postgres silently falls back to SQLite

These are roadmap violations.

---

# 1. Architectural Alignment Rules

All work MUST respect the 7-layer architecture:

```
Layer 7: Observability
Layer 6: API & Streaming
Layer 5: LLM Provider
Layer 4: Agent Runtime
Layer 3: Execution
Layer 2: Persistence
Layer 1: Foundation
```

Dependency direction is strictly top-down.

No lateral or upward imports.

---

# 2. Definition of Done (DoD)

A feature is not complete unless:

* It is listed in ROADMAP.md
* It has a clear layer assignment
* It has observability
* It respects persistence boundaries
* It respects multi-user isolation
* It has tests
* It does not introduce architectural drift

---

# 3. Roadmap Priority Enforcement

## P0 – Table Stakes (Must Complete First)

* Message persistence
* Remove `shell=True`
* Multi-user harness
* Wire rate limiter
* Functional abort
* Enable MLflow autolog

No new features beyond bug fixes are allowed until P0 is complete.

---

## P1 – Production Ready

* Unified StorageBackend
* Postgres support
* Docker sandbox backend
* Declarative AgentDefinition
* AgentRuntime protocol
* Alembic migrations

---

## P2 – Robustness

* SSE reconnection
* Circuit breaker + retries
* Evaluation pipeline foundation
* Proper token accounting

---

## P3 – Full Vision

* MLflow evaluation workflows - see MLFLOW-INTEROPERABILITY.md
* Prompt registry
* Cloud execution backends
* Human feedback loop

---

# 4. Enforcement Protocol

Before merging any PR, agents must verify:

* Is this task in ROADMAP.md?
* Is it the correct priority tier?
* Does it respect layer boundaries?
* Does it move us toward the architecture in ?

If any answer is “no,” the PR must be revised.

---

# 5. Architectural North Star

Cognition must eventually allow:

```python
agent = AgentDefinition(
    tools=[...],
    skills=[...],
    system_prompt="...",
)

app = Cognition(agent)
app.run()
```

And automatically provide:

* REST API
* SSE streaming
* Persistence
* Sandbox isolation
* Observability
* Multi-user scoping
* Evaluation pipeline

The roadmap exists to force convergence toward that state.

