# Cognition

An OpenCode-style coding agent with **persistent multi-session support**, FastAPI WebSocket + Textual TUI.

## Features

- 🔄 **Persistent Projects** - Work survives disconnects and server restarts
- 🧠 **Hybrid Memory** - Fast RAM + persistent disk storage for agent context
- 🐳 **Container-per-Session** - Fresh, isolated execution environment
- 🔌 **WebSocket API** - Real-time bidirectional communication
- 🖥️ **Textual TUI** - Rich terminal interface
- 🔧 **Multi-LLM Support** - OpenAI, Anthropic, AWS Bedrock, local models

## Quick Start

```bash
# Install dependencies
uv pip install -e ".[all]"

# Copy environment config
cp .env.example .env
# Edit .env with your API keys

# Build Docker agent image
make build-agent-image

# Start server
cd server && uv run uvicorn app.main:app --reload --port 8000

# In another terminal, start client
cd client && uv run python -m tui.app

# Create a persistent project
> /create my-project
```

## Architecture

- **Server**: FastAPI WebSocket API with LangGraph Deep Agents runtime
- **Client**: Textual TUI for interactive sessions
- **Execution**: Container-per-session with optional network isolation
- **Persistence**: Projects with hybrid memory (RAM + disk) survive disconnects
- **Scope**: Python repos only, pytest-based testing

### Persistent Multi-Session Support

Cognition now supports persistent projects:
- Projects survive disconnects and server restarts
- Agent memories accumulate across sessions
- Automatic memory snapshots every 5 minutes
- Configurable auto-cleanup (default: 30 days)
- Fresh container on each reconnect (1-2s startup)

## Development

```bash
# Run tests
uv run pytest -q

# Type checking
uv run mypy server/ client/ --strict

# Linting
uv run ruff check server/ client/
uv run ruff format server/ client/
```

## License

MIT
