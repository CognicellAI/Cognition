# Cognition: OpenCode-Style AI Coding Assistant

> A local AI coding assistant inspired by Claude Code and OpenCode, built with LangGraph, FastAPI, and Textual TUI.

## What is Cognition?

Cognition is an open-source, self-hosted coding assistant that helps you understand, analyze, and generate code. Unlike cloud-based alternatives, Cognition runs entirely on your machine with full control over your code and LLM provider.

**Key Features:**
- 🧠 **In-Process Agents** - No containers, no network latency
- 🔌 **Multi-LLM Support** - OpenAI, AWS Bedrock, local models
- 💬 **Persistent Sessions** - Resume conversations across restarts
- 🎨 **Beautiful TUI** - Terminal UI built with Textual
- 🔒 **Privacy-First** - Your code never leaves your machine
- 🛠️ **Extensible** - Built on LangGraph for easy customization

---

## Quick Start (5 minutes)

### Prerequisites

- Python 3.11+
- `uv` package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))
- OpenAI API key **OR** AWS Bedrock access

### 1. Clone and Setup

```bash
git clone https://github.com/yourusername/cognition.git
cd cognition

# Install dependencies
uv pip install -r server/requirements.txt
uv pip install -r client/requirements.txt
```

### 2. Configure LLM

**Option A: OpenAI**
```bash
export OPENAI_API_KEY="sk-..."
export LLM_PROVIDER="openai"
export DEFAULT_MODEL="gpt-4-turbo-preview"
```

**Option B: AWS Bedrock**
```bash
export AWS_PROFILE="your-profile"
export LLM_PROVIDER="bedrock"
export BEDROCK_MODEL_ID="anthropic.claude-3-haiku-20240307-v1:0"
```

### 3. Start the Server

```bash
cd server
uv run uvicorn app.main:app --reload --port 8000
```

Server will be available at `http://localhost:8000`

### 4. Run the TUI Client (in another terminal)

```bash
cd client
uv run python -m tui.app
```

### 5. Create Your First Project

1. Press `Ctrl+P` to open command palette
2. Select "Create project"
3. Enter project name (e.g., "my-code")
4. Start chatting with the AI!

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    COGNITION                             │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  TUI Client (Textual)                                    │
│  └── Terminal-based chat interface                       │
│      ├── Project management                              │
│      ├── Session history                                 │
│      └── Code display & formatting                       │
│                                                           │
│  FastAPI Server (Port 8000)                              │
│  ├── REST API (/api/projects, /api/sessions)             │
│  ├── WebSocket (/ws) for real-time chat                  │
│  └── OTEL Observability (optional)                       │
│                                                           │
│  In-Process Agent                                        │
│  ├── LangGraph State Machine (context management)        │
│  ├── LLM Router (OpenAI, Bedrock, local)                 │
│  └── Project Workspace Management                        │
│                                                           │
│  Storage Layer                                           │
│  ├── Projects (file-based)                               │
│  ├── Sessions & History (SQLite-ready)                   │
│  └── Workspace (local filesystem)                        │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

---

## Usage Guide

### Creating a Project

A project is a container for your code and conversation history.

```bash
# Via TUI: Ctrl+P → "Create project"

# Via API:
curl -X POST http://localhost:8000/api/projects \
  -H "Content-Type: application/json" \
  -d '{
    "user_prefix": "my-project",
    "description": "Analyze my web scraper",
    "tags": ["python", "automation"]
  }'
```

### Starting a Session

A session is an active conversation about a project.

```bash
# Via TUI: Select project → Start session

# Via API:
curl -X POST http://localhost:8000/api/projects/{project_id}/sessions \
  -H "Content-Type: application/json" \
  -d '{"network_mode": "OFF"}'
```

### Chatting with the Agent

Once connected via WebSocket, send messages:

```json
{
  "type": "user_message",
  "session_id": "xxx",
  "content": "Explain what this code does: [code snippet]"
}
```

Agent responds with analysis, suggestions, or generated code.

---

## Configuration

### Environment Variables

```bash
# Server
HOST=0.0.0.0                    # Server bind address
PORT=8000                        # Server port
LOG_LEVEL=info                   # Logging level

# LLM Provider (choose one)
LLM_PROVIDER=openai              # or "bedrock", "openai_compatible"
OPENAI_API_KEY=sk-...            # For OpenAI
DEFAULT_MODEL=gpt-4-turbo        # OpenAI model

# AWS Bedrock
AWS_PROFILE=default              # or use AWS_ACCESS_KEY_ID/SECRET
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=anthropic.claude-3-haiku-...

# Observability (optional)
OTEL_ENABLED=false
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317

# Workspace
WORKSPACE_ROOT=./workspaces      # Where projects are stored
MAX_SESSIONS=100
```

### Create `.env` file

```bash
# .env (in project root)
OPENAI_API_KEY=sk-your-key-here
LLM_PROVIDER=openai
DEFAULT_MODEL=gpt-4-turbo-preview
LOG_LEVEL=info
```

---

## Development

### Running Tests

```bash
# All unit tests
uv run pytest tests/ --ignore=tests/e2e -v

# Specific test file
uv run pytest tests/test_client_api.py -v

# With coverage
uv run pytest tests/ --cov=server --cov=client
```

### Type Checking

```bash
uv run mypy server/ client/ --strict
```

### Formatting & Linting

```bash
# Format code
uv run ruff format server/ client/

# Check linting
uv run ruff check server/ client/
```

---

## Project Structure

```
cognition/
├── server/
│   └── app/
│       ├── agent/              # In-process agent runtime
│       │   ├── runtime.py       # LangGraph-based agent
│       │   ├── llm_handler.py   # LLM provider routing
│       │   └── manager.py       # Agent lifecycle
│       ├── sessions/            # Session management
│       ├── projects/            # Project metadata
│       ├── main.py              # FastAPI app
│       └── settings.py          # Configuration
├── client/
│   └── tui/
│       ├── app.py               # Textual application
│       ├── screens/             # TUI screens
│       ├── widgets/             # UI components
│       ├── api.py               # REST client
│       └── websocket.py         # WebSocket client
├── tests/
│   ├── test_client_*.py         # Client tests
│   ├── test_backend_*.py        # Backend tests
│   └── e2e/                     # End-to-end tests
└── README.md
```

---

## Roadmap

### Phase 1 ✅ Complete
- [x] Remove Docker container complexity
- [x] Implement in-process agent with LangGraph
- [x] Multi-LLM support (OpenAI, Bedrock)
- [x] Session persistence foundation

### Phase 2 (Current)
- [ ] OTEL observability integration
- [ ] Settings/preferences UI
- [ ] Code analysis tools (file read, search)
- [ ] Project templates

### Phase 3
- [ ] Web UI (React-based dashboard)
- [ ] Advanced context management
- [ ] Tool system (bash execution, file editing)
- [ ] Marketplace for custom agents

---

## Troubleshooting

### "No LLM configured" Error

**Solution:** Set `OPENAI_API_KEY` or AWS credentials:
```bash
export OPENAI_API_KEY="sk-..."
export LLM_PROVIDER="openai"
```

### WebSocket Connection Failed

**Solution:** Ensure server is running:
```bash
# Terminal 1
cd server && uv run uvicorn app.main:app --port 8000

# Terminal 2 (wait 2 seconds)
cd client && uv run python -m tui.app
```

### Tests Failing

**Solution:** Install test dependencies:
```bash
uv pip install -e ".[dev]"
uv run pytest tests/
```

---

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes with tests
4. Run type checking and linting
5. Commit with clear messages
6. Push and open a Pull Request

---

## License

MIT - See LICENSE file for details

---

## Support & Community

- 💬 **GitHub Discussions** - Ask questions and share ideas
- 🐛 **Issue Tracker** - Report bugs
- 📖 **Documentation** - Full docs at `docs/`
- 💭 **Design Discussions** - ADRs in `docs/adr/`

---

## Credits

Built with:
- [LangGraph](https://python.langchain.com/docs/langgraph/) - Agent orchestration
- [FastAPI](https://fastapi.tiangolo.com/) - Web framework
- [Textual](https://textual.textualize.io/) - TUI framework
- [Pydantic](https://docs.pydantic.dev/) - Data validation

Inspired by:
- [Claude Code](https://www.anthropic.com/) - Anthropic's code interpreter
- [OpenCode](https://github.com/codelion/OpenCode) - Open-source coding assistant
- [Aider](https://aider.chat/) - Git-aware AI coding assistant
