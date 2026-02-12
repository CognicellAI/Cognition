# Cognition

> OpenCode-style AI coding assistant. Local. Fast. Extensible.

An open-source, self-hosted coding assistant inspired by Claude Code and OpenCode, built with **LangGraph**, **FastAPI**, and **Textual TUI**.

## Features

- 🧠 **LangGraph-Powered** - Advanced context management with state machines
- 🔌 **Multi-LLM Support** - OpenAI, AWS Bedrock, OpenAI-compatible APIs
- 💬 **Persistent Sessions** - Resume conversations across restarts
- 🎨 **Beautiful TUI** - Terminal UI built with Textual
- ⚡ **In-Process Agent** - No Docker overhead, immediate responses
- 🔒 **Privacy-First** - Code never leaves your machine
- 📊 **OTEL Ready** - Built-in observability for production deployments

## Quick Start

See [GETTING_STARTED.md](./GETTING_STARTED.md) for detailed setup instructions.

### 60-Second Setup

```bash
# Install dependencies
uv pip install -r server/requirements.txt
uv pip install -r client/requirements.txt

# Configure LLM (OpenAI example)
export OPENAI_API_KEY="sk-your-key"
export LLM_PROVIDER="openai"

# Start server (Terminal 1)
cd server && uv run uvicorn app.main:app --reload --port 8000

# Start client (Terminal 2)
cd client && uv run python -m tui.app
```

## Architecture

```
TUI Client ←→ WebSocket ←→ FastAPI Server ←→ In-Process Agent ←→ LLM
  (Textual)                 (Port 8000)        (LangGraph)      (OpenAI/Bedrock)
```

**Key Design Decisions:**
- ✅ In-process agents (no Docker complexity)
- ✅ LangGraph for context management
- ✅ Simple REST + WebSocket API
- ✅ File-based projects, SQLite-ready for sessions
- ✅ Support for client-server separation (PaaS-ready)

## Project Structure

```
cognition/
├── server/app/              # FastAPI + agent runtime
│   ├── agent/               # InProcessAgent (LangGraph)
│   ├── sessions/            # Session lifecycle
│   ├── projects/            # Project metadata
│   └── main.py              # API endpoints
├── client/tui/              # Textual TUI
│   ├── screens/             # Main screens
│   ├── widgets/             # UI components
│   ├── api.py               # REST client
│   └── websocket.py         # WebSocket handler
├── tests/                   # 156+ unit tests
└── docs/                    # Architecture & guides
```

## Development

```bash
# Run all tests
uv run pytest tests/ --ignore=tests/e2e -v

# Type checking
uv run mypy server/ client/ --strict

# Format & lint
uv run ruff format server/ client/
uv run ruff check server/ client/
```

## Configuration

See `.env.example` for all options:

```bash
# LLM Provider
LLM_PROVIDER=openai              # or "bedrock", "openai_compatible"
OPENAI_API_KEY=sk-...
DEFAULT_MODEL=gpt-4-turbo-preview

# Server
PORT=8000
LOG_LEVEL=info
```

## Status

- ✅ Phase 1: In-process agent architecture (COMPLETE)
- ✅ Phase 2: TUI client integration (COMPLETE)  
- 🚀 Phase 3: Documentation & polish (IN PROGRESS)

## Roadmap

- [ ] OTEL observability integration
- [ ] Code analysis tools (file read, search, etc)
- [ ] Web UI (React)
- [ ] Tool system (bash, file editing)
- [ ] Agent templates
- [ ] Multi-provider billing

## Contributing

Contributions welcome! Please read [CONTRIBUTING.md](./CONTRIBUTING.md) first.

## License

MIT

---

**Quick Links:**
- 📖 [Getting Started Guide](./GETTING_STARTED.md)
- 🏗️ [Architecture Deep Dive](./docs/ARCHITECTURE.md)
- 🧪 [Testing Guide](./docs/TESTING.md)
- 🐛 [Troubleshooting](./GETTING_STARTED.md#troubleshooting)
