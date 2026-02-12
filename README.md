# Cognition

A local AI coding assistant built on **LangGraph Deep Agents**.

## Quick Start

```bash
# Install dependencies
uv sync

# Set your LLM API key (optional - defaults to mock mode)
export OPENAI_API_KEY="sk-..."

# Start the server (Terminal 1)
uv run python -m server.app.main

# Start the TUI client (Terminal 2)
uv run python -m client.tui.app
```

## Architecture

```
TUI Client ←→ WebSocket ←→ FastAPI Server ←→ Deep Agent ←→ LLM
                           (In-Process)        (LocalSandbox)
```

## Key Features

- **In-process execution**: No Docker containers, runs directly on your machine
- **Deep Agents**: Built-in file tools (`ls`, `read_file`, `write_file`, `edit_file`, `grep`, `glob`)
- **Command execution**: Full bash access via `LocalSandbox`
- **Streaming**: Real-time token and tool call events over WebSocket
- **Session persistence**: Conversations resume automatically
- **CompositeBackend**: Ephemeral workspace + persistent memories

## Project Structure

```
cognition/
├── server/app/
│   ├── main.py          # FastAPI + WebSocket
│   ├── agent.py         # Deep Agent factory
│   ├── sandbox.py       # LocalSandbox backend
│   ├── sessions.py      # Session management
│   └── settings.py      # Configuration
├── client/tui/
│   ├── app.py           # Textual interface
│   └── api.py           # REST client
├── shared/__init__.py   # Protocol messages
└── tests/
    └── unit/test_sandbox.py
```

## Configuration

Environment variables (`.env`):

```
COGNITION_LLM_PROVIDER=openai  # or "bedrock" or "mock"
COGNITION_LLM_MODEL=gpt-4o
OPENAI_API_KEY=sk-...

# Optional
COGNITION_HOST=127.0.0.1
COGNITION_PORT=8000
COGNITION_WORKSPACE_ROOT=./workspaces
```

## Testing

```bash
# Unit tests (fast, no LLM calls)
uv run pytest tests/unit/ -v

# E2E tests (requires running server)
COGNITION_TEST_LLM_MODE=mock uv run pytest tests/e2e/ -v

# E2E with real OpenAI
export OPENAI_API_KEY="sk-..."
COGNITION_TEST_LLM_MODE=openai uv run pytest tests/e2e/ -v
```

## Future: Multi-Tenancy on Kubernetes

See `docs/design-part2-multi-tenancy.md` for the K8s operator-based architecture supporting multiple tenants with namespace isolation, RBAC, and NetworkPolicy.

## License

MIT
