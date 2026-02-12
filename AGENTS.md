# AGENTS.md — Cognition Coding Agent

Guidelines for agentic coding assistants working on this codebase.

## Project Overview

Cognition is an OpenCode-style coding agent with:
- **Server**: FastAPI WebSocket API with Deep Agents runtime
- **Client**: Textual TUI for interactive sessions
- **Execution**: Container-per-session with optional network isolation
- **Scope**: Python repos only, pytest-based testing

## Build / Test / Lint Commands

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv pip install -r server/requirements.txt
uv pip install -r client/requirements.txt

# Or use uv run (creates venv automatically)
uv run --with-requirements server/requirements.txt python -m uvicorn app.main:app --reload --port 8000

# Run server (development)
cd server && uv run uvicorn app.main:app --reload --port 8000

# Run client (development)
cd client && uv run python -m tui.app

# Run all tests
uv run pytest -q

# Run single test file
uv run pytest tests/test_specific.py -v

# Run single test
uv run pytest tests/test_file.py::test_function_name -v

# Type checking
uv run mypy server/ client/ --strict

# Linting (use ruff)
uv run ruff check server/ client/
uv run ruff format server/ client/

# Docker build
make build-agent-image
```

## Code Style Guidelines

### Python Standards
- **Python 3.11+** required
- Use **type hints everywhere** — strict mypy compliance
- **Docstrings**: Google style for all public functions/classes
- **Max line length**: 100 characters
- **Import style**: isort-compatible, grouped as stdlib, third-party, local

### Naming Conventions
- `snake_case` for functions, variables, modules
- `PascalCase` for classes
- `UPPER_CASE` for constants
- Private methods prefix with `_`
- Async functions prefix with verbs: `get_`, `fetch_`, `handle_`

### Async Patterns
- **Always use async/await** for I/O operations
- WebSocket handlers must be async
- Container execution uses asyncio subprocess
- Use `asyncio.gather()` for parallel operations

### Error Handling
- Use **custom exception hierarchy** in `app/exceptions.py`
- Wrap external calls with try/except → raise domain exceptions
- Log all errors with structured logging (OpenTelemetry)
- Never swallow exceptions silently
- Use `raise from` when chaining exceptions

### Type Safety
- Use Pydantic v2 for all data models
- Prefer `Optional[T]` over `T | None` for clarity
- Use `Sequence[T]` over `list[T]` for parameters
- Return concrete types, accept abstract types

## Project Structure

```
cognition/
├── server/app/          # FastAPI + WebSocket + agents
│   ├── sessions/        # Session lifecycle management
│   ├── agent/           # Deep Agents runtime
│   ├── tools/           # Tool interfaces (filesystem, search, etc.)
│   ├── executor/        # Container execution layer
│   ├── protocol/        # Message/event models
│   └── streaming/       # Event streaming
├── client/tui/          # Textual TUI
│   └── widgets/         # Custom UI components
└── shared/              # Shared protocol schemas
```

## Key Patterns

### WebSocket Message Flow
1. Client sends `user_msg` → Server validates
2. Server routes to Session Manager → Deep Agent
3. Agent requests tool → Tool Mediator validates
4. Container Executor runs → streams output
5. Events stream back to client

### Container Execution
- **argv only** — no shell strings (`["pytest", "-q"]` not `"pytest -q"`)
- **Network OFF by default** — explicit opt-in required
- **Workspace mounted** at `/workspace/repo`
- **Timeouts enforced** on all operations

### Tool Safety
- Validate all paths stay within workspace
- No arbitrary command execution
- Diff-based editing only (unified diff format)
- Read-only for inspection tools

## Testing Guidelines

- **Unit tests**: Fast, no containers, mocked dependencies
- **Integration tests**: Use test containers, mark with `@pytest.mark.integration`
- **E2E tests**: Full WebSocket round-trip tests
- Mock external APIs (LLM calls) in unit tests
- Use `tmp_path` fixture for filesystem operations
- Test both network ON and OFF modes

## Docker Guidelines

- Run as non-root user (UID 1000)
- Read-only root filesystem where possible
- No sensitive data in images
- Use multi-stage builds for smaller images

## Security Reminders

- Never log secrets or API keys
- Validate all user inputs at boundaries
- Path traversal protection on all file operations
- Container resource limits (CPU, memory, output size)
- No network access unless explicitly enabled

## Environment Setup

```bash
# Required env vars
cp .env.example .env
# Edit: OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.

# Pre-commit hooks
pre-commit install
```

## Common Tasks

```bash
# Add new tool
# 1. Define in app/tools/
# 2. Add to Tool Mediator
# 3. Register in agent policies
# 4. Add tests

# Update protocol schema
# Edit shared/protocol/schema.json
# Regenerate models: make generate-models

# Debug session
# Enable debug logging: LOG_LEVEL=debug uvicorn ...
# View container logs: docker logs <container_id>
```
