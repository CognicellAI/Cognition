# Cognition

A local AI coding assistant built on **LangGraph Deep Agents** with a modern REST API and Server-Sent Events streaming.

## Overview

Cognition provides an AI-powered coding assistant that runs locally on your machine. It features:

- **REST API** with OpenAPI 3.1 documentation
- **Server-Sent Events (SSE)** for real-time streaming responses
- **Multi-LLM support** (OpenAI, AWS Bedrock, OpenAI-compatible endpoints)
- **In-process execution** via LocalSandbox (no Docker required)
- **Session management** with automatic checkpointing
- **YAML configuration** with hierarchical loading

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/cognition.git
cd cognition

# Install dependencies (using uv)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync

# Or using pip
pip install -e ".[all]"
```

### Configuration

Create a configuration file at `~/.cognition/config.yaml`:

```yaml
llm:
  provider: openai
  model: gpt-4o
  temperature: 0.7

server:
  host: 127.0.0.1
  port: 8000
```

Or use environment variables:

```bash
export OPENAI_API_KEY="sk-..."
export COGNITION_LLM_PROVIDER="openai"
export COGNITION_LLM_MODEL="gpt-4o"
```

### Running the Server

```bash
# Start the API server
cognition serve

# Or with options
cognition serve --host 0.0.0.0 --port 8080

# View logs
cognition serve --log-level debug
```

The server will be available at `http://127.0.0.1:8000` with:
- REST API: `http://127.0.0.1:8000`
- OpenAPI Docs: `http://127.0.0.1:8000/docs`
- Health Check: `http://127.0.0.1:8000/health`

### Using the TUI Client

```bash
# In a new terminal
cognition-client

# Or with custom server URL
COGNITION_SERVER_URL=http://127.0.0.1:8000 cognition-client
```

### Using the API Directly

```bash
# Create a project
curl -X POST http://127.0.0.1:8000/projects \
  -H "Content-Type: application/json" \
  -d '{"name": "my-project", "description": "Test project"}'

# Create a session
curl -X POST http://127.0.0.1:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"project_id": "<project-id>", "title": "My Session"}'

# Send a message (streams SSE)
curl -X POST http://127.0.0.1:8000/sessions/<session-id>/messages \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"content": "Hello, what files are in this project?"}'
```

## Architecture

```
┌──────────────┐     HTTP REST      ┌──────────────────┐
│   TUI/CLI    │ ◄────────────────► │   FastAPI Server │
│   Client     │    SSE Streaming   │                  │
└──────────────┘                    │   ┌───────────┐  │
                                    │   │  Projects │  │
┌──────────────┐                    │   │  Sessions │  │
│  Web Browser │ ────/docs────────► │   │  Messages │  │
│  (OpenAPI)   │                    │   └───────────┘  │
└──────────────┘                    │         │        │
                                    │         ▼        │
                                    │   ┌───────────┐  │
                                    │   │   Agent   │  │
                                    │   │  (LLM)    │  │
                                    │   └───────────┘  │
                                    │         │        │
                                    │         ▼        │
                                    │   ┌───────────┐  │
                                    │   │ LocalSandbox│ │
                                    │   │  (Execution)│ │
                                    │   └───────────┘  │
                                    └──────────────────┘
```

## Features

### REST API

Full REST API with automatic OpenAPI documentation:

- **Projects**: Create, list, manage project workspaces
- **Sessions**: Create, configure, and manage agent sessions
- **Messages**: Send messages and receive streaming responses via SSE
- **Configuration**: View and update server settings

### Server-Sent Events (SSE)

Messages return streaming events:

```
event: token
data: {"content": "Hello"}

event: tool_call
data: {"name": "glob", "args": {"pattern": "*.py"}, "id": "123"}

event: tool_result
data: {"tool_call_id": "123", "output": "main.py app.py", "exit_code": 0}

event: usage
data: {"input_tokens": 10, "output_tokens": 50, "estimated_cost": 0.002}

event: done
data: {}
```

### Configuration System

Hierarchical configuration (lowest to highest precedence):

1. Built-in defaults
2. `~/.cognition/config.yaml` (global user settings)
3. `.cognition/config.yaml` (project-specific)
4. Environment variables / `.env` file

Example `~/.cognition/config.yaml`:

```yaml
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
  system_prompt: "You are a helpful coding assistant."
  max_iterations: 15

workspace:
  root: ./workspaces

rate_limit:
  per_minute: 60
  burst: 10
```

### CLI Commands

```bash
# Server management
cognition serve                    # Start server
cognition serve --port 8080       # Custom port
cognition serve --reload          # Development mode

# Configuration
cognition init                     # Create config files
cognition config                   # Show current config

# Health check
cognition health                   # Check server status
```

## API Reference

The complete API reference is available at `http://localhost:8000/docs` when the server is running.

### Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/projects` | Create a new project |
| `GET` | `/projects` | List all projects |
| `POST` | `/sessions` | Create a new session |
| `GET` | `/sessions` | List sessions |
| `POST` | `/sessions/:id/messages` | Send message (SSE stream) |
| `GET` | `/sessions/:id/messages` | List messages |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | OpenAPI documentation |

## Testing

```bash
# Run all tests
pytest

# Run only unit tests (fast)
pytest tests/unit/ -v

# Run E2E tests (requires server)
pytest tests/e2e/ -v

# Run with coverage
pytest --cov=server --cov=client --cov-report=html
```

## Development

```bash
# Install in development mode
pip install -e ".[dev]"

# Run linting
ruff check server/ client/

# Run type checking
mypy server/ client/

# Format code
ruff format server/ client/
```

## Configuration Reference

See [docs/configuration.md](docs/configuration.md) for detailed configuration options.

## Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md) for common issues and solutions.

## Project Structure

```
cognition/
├── server/app/
│   ├── main.py              # FastAPI application
│   ├── api/                 # REST API routes
│   │   ├── models.py        # Pydantic models
│   │   ├── sse.py           # SSE utilities
│   │   └── routes/          # API endpoints
│   ├── agent/               # Agent tools and workflows
│   ├── config_loader.py     # YAML configuration
│   ├── cli.py               # Typer CLI
│   └── settings.py          # Settings management
├── client/tui/
│   ├── app.py               # Textual TUI
│   └── api.py               # REST client with SSE
├── tests/
│   ├── unit/                # Unit tests
│   └── e2e/                 # End-to-end tests
├── docs/                    # Documentation
└── README.md
```

## Roadmap

- **Phase 5** (Current): REST API, SSE streaming, YAML config, CLI ✅
- **Phase 6**: Production hardening, authentication, Docker deployment
- **Phase 7**: Multi-tenancy, Kubernetes operator, enterprise features

See [ROADMAP.md](ROADMAP.md) for detailed planning.

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions welcome! Please read our contributing guidelines and submit PRs.

## Support

- Documentation: `http://localhost:8000/docs` (when server is running)
- Issues: [GitHub Issues](https://github.com/yourusername/cognition/issues)
- Discussions: [GitHub Discussions](https://github.com/yourusername/cognition/discussions)
