# Cognition User Guide

Complete guide to install, configure, and run Cognition - an AI-powered coding agent.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [Running Cognition](#running-cognition)
5. [Using the TUI](#using-the-tui)
6. [Projects and Persistent Sessions](#projects-and-persistent-sessions)
7. [Supported LLM Providers](#supported-llm-providers)
8. [Examples](#examples)
9. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Software

- **Python**: 3.11 or higher
- **Docker**: Running and accessible (install from [docker.com](https://docker.com))
- **Git**: For cloning repositories
- **uv**: Python package manager (we'll install this)

### System Requirements

- **RAM**: 4GB minimum (8GB+ recommended)
- **Disk**: 20GB free space (for workspaces and containers)
- **Network**: Internet connection for LLM API calls

### Verify Prerequisites

```bash
# Check Python version
python3 --version

# Check Docker is running
docker ps

# Check Git is installed
git --version
```

---

## Installation

### Step 1: Clone the Repository

```bash
git clone https://github.com/yourusername/cognition.git
cd cognition
```

### Step 2: Install uv (Universal Python Package Manager)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Add uv to your PATH (follow the instructions from the installation script).

### Step 3: Install Dependencies

```bash
# Install all dependencies (server + client + dev tools)
uv pip install -e ".[all]"
```

This installs:
- FastAPI, Uvicorn (server)
- Textual (TUI client)
- LangGraph, LangChain (AI runtime)
- pytest, ruff, mypy (dev tools)

### Step 4: Build Docker Agent Image

```bash
# Build the container image used for isolated code execution
make build-agent-image
```

Or manually:
```bash
docker build -t opencode-agent:py -f docker/Dockerfile.agent .
```

Verify the image was created:
```bash
docker images | grep opencode-agent
```

---

## Configuration

### Step 1: Create `.env` File

```bash
cp .env.example .env
```

### Step 2: Choose Your LLM Provider

Edit `.env` and configure one of the following:

#### Option A: OpenAI

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-your-key-here
DEFAULT_MODEL=gpt-4-turbo-preview
```

Get your API key from https://platform.openai.com/api-keys

#### Option B: Anthropic (Claude)

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-key-here
DEFAULT_MODEL=claude-3-opus-20240229
```

Get your API key from https://console.anthropic.com

#### Option C: AWS Bedrock

**Method 1: IAM Role (Recommended for AWS environments)**
```bash
LLM_PROVIDER=bedrock
USE_BEDROCK_IAM_ROLE=true
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=anthropic.claude-3-sonnet-20240229-v1:0
```

**Method 2: Explicit AWS Credentials**
```bash
LLM_PROVIDER=bedrock
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=anthropic.claude-3-sonnet-20240229-v1:0
```

**Method 3: AWS Profile**
```bash
LLM_PROVIDER=bedrock
AWS_PROFILE=default
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=anthropic.claude-3-sonnet-20240229-v1:0
```

#### Option D: OpenAI-Compatible API (LiteLLM, vLLM, Ollama, etc.)

```bash
# For local Ollama
LLM_PROVIDER=openai_compatible
OPENAI_API_BASE=http://localhost:11434/v1
DEFAULT_MODEL=llama2
# OPENAI_API_KEY=optional-if-local

# For LiteLLM Proxy
LLM_PROVIDER=openai_compatible
OPENAI_API_BASE=http://localhost:8000/v1
OPENAI_API_KEY=sk-...
DEFAULT_MODEL=gpt-4

# For vLLM
LLM_PROVIDER=openai_compatible
OPENAI_API_BASE=http://localhost:8000/v1
DEFAULT_MODEL=mistral-7b-instruct-v0.2
```

### Step 3: Verify Configuration

```bash
# Test that settings are loaded
uv run python -c "from server.app.settings import get_settings; s = get_settings(); print(f'Provider: {s.llm_provider}, Model: {s.default_model}')"
```

---

## Running Cognition

### Terminal 1: Start the Server

```bash
make dev-server
```

Or manually:
```bash
cd server && uv run uvicorn app.main:app --reload --port 8000
```

You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete
```

The server is now listening on `ws://localhost:8000/ws`

### Terminal 2: Start the Client (TUI)

In a new terminal:

```bash
make dev-client
```

Or manually:
```bash
cd client && uv run python -m tui.app
```

You should see the Cognition TUI interface:
```
┌──────────────────────────────────────────────────────────────┐
│ Cognition v0.1.0                                            │
├──────────────────────────────────────────────────────────────┤
│ Session: None | Network: OFF | Workspace: None              │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│                                                              │
│                                                              │
│ > 
└──────────────────────────────────────────────────────────────┘
```

### Verify Connection

The TUI should show:
- Connection status in the banner
- `/create` command available in the prompt

---

## Using the TUI

### Creating a Session

```
> /create
```

Or send a create_session message to start working with a repository.

**What happens:**
- Docker container is created for this session
- Workspace directory is initialized
- You're ready to give commands

### Session Banner Shows:
```
Session: a1b2c3d4 | Network: OFF | Workspace: ./workspaces/a1b2c3d4/repo
```

### Commands

| Command | Purpose | Example |
|---------|---------|---------|
| `/create` | Start new session with empty workspace | `/create` |
| `/clone <url>` | Clone a git repo into workspace | `/clone https://github.com/user/repo.git` |
| Regular text | Send message to agent | `Show me all Python files` |
| `Ctrl+C` | Cancel current operation | Cancel long-running agent |
| `q` or `/quit` | Exit TUI | Quit TUI (keeps server running) |

### Natural Language Examples

Once a session is created, you can ask things like:

```
> Find all Flask routes in this codebase
> Show me the test failures for this repo
> Create a migration plan to convert this to FastAPI
> Apply this diff to update the authentication
> Run the full test suite
> What's the git status?
> Search for all database queries
> ```

---

## Projects and Persistent Sessions

Cognition now supports **persistent multi-session projects**. This means your work is preserved across disconnects, and you can resume anytime.

### What is a Project?

A project is a persistent workspace that:
- ✅ Survives disconnects and server restarts
- ✅ Accumulates agent memories across sessions
- ✅ Supports multiple sessions over time
- ✅ Has automatic cleanup (configurable)
- ✅ Can be pinned to prevent deletion

### Project Structure

```
workspaces/
  my-project-a7b3c2d1/          # Project directory
    repo/                        # Your code (persisted)
    .memories/
      hot/                       # Recent context (RAM)
      persistent/                # Long-term memory (disk)
    .logs/                       # Session logs
    .project_metadata.json       # Project info
```

### Creating a Project

When you create a session, you can specify a project:

**Option 1: Create with a user prefix**
```
> /create my-api
```
This creates a project like `my-api-a7b3c2d1`

**Option 2: Resume existing project**
```
> /resume my-api-a7b3c2d1
```

**Option 3: Create without a project (ephemeral)**
```
> /create
```
Session is deleted when you disconnect (old behavior)

### Project Naming

Project IDs follow the format: `{prefix}-{uuid}`

- **Prefix rules**: Lowercase, start with letter, alphanumeric + hyphens, max 32 chars
- **Examples**: `payment-api-a7b3c2d1`, `frontend-app-f4e5d6c7`

### Memory Persistence

Cognition uses a **hybrid memory strategy**:

| Memory Type | Location | Speed | Persistence |
|-------------|----------|-------|-------------|
| Hot memories | RAM | 0.001ms | Snapshotted every 5 min |
| Persistent memories | Disk | 0.036ms | Permanent |
| Workspace files | Disk | 0.036ms | Permanent |

**How it works:**
1. Agent stores context in `/memories/hot/` (fast RAM)
2. Every 5 minutes, hot memories are snapshotted to disk
3. On disconnect, final snapshot is saved
4. On reconnect, memories are restored from disk
5. Long-term memories go directly to `/memories/persistent/`

### Managing Projects via API

**List all projects:**
```bash
curl http://localhost:8000/api/projects
```

**Create a project:**
```bash
curl -X POST http://localhost:8000/api/projects \
  -H "Content-Type: application/json" \
  -d '{
    "user_prefix": "my-api",
    "network_mode": "OFF",
    "repo_url": "https://github.com/user/repo"
  }'
```

**Pin a project** (prevent auto-cleanup):
```bash
curl -X POST http://localhost:8000/api/projects/my-api-a7b3c2d1/extend \
  -H "Content-Type: application/json" \
  -d '{"pin": true}'
```

**List resumable sessions** (disconnected projects you can reconnect to):
```bash
# List all projects that can be resumed
curl http://localhost:8000/api/sessions/resumable

# Response shows projects with their last session info:
{
  "sessions": [
    {
      "project_id": "my-api-a7b3c2d1",
      "user_prefix": "my-api",
      "last_accessed": "2026-02-10T14:30:00Z",
      "last_session_duration_seconds": 3600,
      "total_messages": 42,
      "workspace_path": "/workspaces/my-api-a7b3c2d1/repo",
      "has_memories": true,
      "cleanup_in_days": 28
    }
  ],
  "total": 1,
  "message": "Found 1 project(s) ready to resume"
}
```

**Filter projects by status:**
```bash
# Only show active projects (currently connected)
curl "http://localhost:8000/api/projects?status=active"

# Only show idle/resumable projects (disconnected but preserved)
curl "http://localhost:8000/api/projects?status=resumable"

# Only show pinned projects
curl "http://localhost:8000/api/projects?status=pinned"
```

**Delete a project:**
```bash
curl -X DELETE http://localhost:8000/api/projects/my-api-a7b3c2d1
```

### Auto-Cleanup

Projects are automatically cleaned up after **30 days of inactivity** (configurable).

**How it works:**
- Day 27: Warning logged
- Day 28: Reminder logged  
- Day 29: Final warning
- Day 30: Project marked for deletion
- Day 31: Deleted if not accessed

**To prevent cleanup:**
1. **Pin the project**: Projects marked as "pinned" are never deleted
2. **Regular access**: Opening the project resets the 30-day timer
3. **Extend lifetime**: Call the extend API to add more days

### Container Lifecycle

Each time you connect to a project:
1. **Fresh container created** (~1-2 seconds)
2. **Workspace mounted** (zero-copy, instant)
3. **Memories restored** from disk snapshot
4. **Agent resumes** with full context

When you disconnect:
1. **Memories snapshotted** to disk
2. **Container stopped** (resources freed)
3. **Project preserved** on disk

**Benefits:**
- Clean state every session (no leftover processes)
- Resource efficient (containers only run when active)
- Fast reconnect (1-2 second container startup)

### Configuration

**Project settings in `.env`:**

```bash
# Maximum projects
MAX_PROJECTS=1000

# Auto-cleanup
PROJECT_CLEANUP_ENABLED=true
PROJECT_CLEANUP_AFTER_DAYS=30
PROJECT_CLEANUP_WARNING_DAYS=3

# Memory snapshots
MEMORY_SNAPSHOT_ENABLED=true
MEMORY_SNAPSHOT_INTERVAL=300  # 5 minutes

# Container lifecycle
CONTAINER_STOP_ON_DISCONNECT=true
CONTAINER_RECREATE_ON_RECONNECT=true

# Backend routes (hybrid memory strategy)
AGENT_BACKEND_ROUTES='{
  "/workspace/": {"type": "filesystem"},
  "/memories/hot/": {"type": "store"},
  "/memories/persistent/": {"type": "filesystem"},
  "/tmp/": {"type": "state"}
}'
```

### Best Practices

**✅ Do:**
- Use descriptive prefixes: `payment-api`, `frontend-v2`, `ml-pipeline`
- Pin important projects
- Organize with tags (via API)
- Let auto-cleanup delete old projects

**❌ Don't:**
- Create too many ephemeral sessions (use projects instead)
- Worry about disconnecting (work is preserved)
- Manually delete workspace directories (use API)

### Example Workflow

```bash
# Day 1: Start working on payment API
> /create payment-api
Project created: payment-api-a7b3c2d1

# Work for a few hours...
> Implement the payment processing endpoint
> Add tests for the new endpoint
> Run the test suite

# Disconnect (work is automatically saved)
# Container stops, workspace preserved, memories snapshotted

# Day 2: Resume where you left off
> /resume payment-api-a7b3c2d1
# Fresh container starts, memories restored, ready to continue

# Continue working...
> Add Stripe integration
> Refactor error handling

# Pin the project to prevent cleanup
> /pin payment-api-a7b3c2d1
```

---

## Supported LLM Providers

### 1. OpenAI (Recommended for MVP)

**Pros:** Fastest, most reliable, works out of box
**Cons:** Requires API key (paid)
**Cost:** $0.01-0.10 per 1K tokens (varies by model)

**Setup:**
```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

### 2. Anthropic Claude

**Pros:** Excellent reasoning, long context windows
**Cons:** Slower than OpenAI
**Cost:** $0.003-0.024 per 1K tokens

**Setup:**
```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

### 3. AWS Bedrock

**Pros:** Enterprise security, multiple model options, no rate limits
**Cons:** Requires AWS account
**Cost:** Pay-per-token (varies by model)

**Setup:**
```bash
LLM_PROVIDER=bedrock
USE_BEDROCK_IAM_ROLE=true  # or explicit credentials
```

### 4. Local/Open-Source (Ollama, vLLM, LiteLLM)

**Pros:** Free, runs locally, no API keys needed, privacy
**Cons:** Slower, requires local hardware
**Cost:** Free (but your hardware)

**Setup Ollama (easiest):**
```bash
# Install Ollama from ollama.ai
# Run: ollama run llama2
# Then configure:
LLM_PROVIDER=openai_compatible
OPENAI_API_BASE=http://localhost:11434/v1
DEFAULT_MODEL=llama2
```

---

## Examples

### Example 1: Refactor Flask to FastAPI

```bash
# Terminal 1: Start server
make dev-server

# Terminal 2: Start TUI
make dev-client

# In TUI:
> /create
Creating session...
Session: xyz123 | Network: OFF | Workspace: ./workspaces/xyz123/repo

> /clone https://github.com/example/flask-app.git
Cloning repository...

> Analyze this Flask app and create a migration plan to FastAPI
[Agent searches, reads files, generates plan]

> Apply the migration to the main app.py file
[Agent generates diff and applies changes]

> Run the test suite to verify
[Agent runs pytest and shows results]
```

### Example 2: Find and Fix a Bug

```
> Create session with my-project.git
> Search for all TODO comments
> Show me the recent git history
> Find where this variable is used
> Apply this bugfix patch
> Run specific test: pytest tests/test_auth.py::test_login
> Show me what changed with git diff
```

### Example 3: Code Review

```
> Clone the PR repository
> Find all functions longer than 100 lines
> Show security-related imports
> Find hardcoded credentials
> Check for missing error handling
> Summary of findings
```

---

## Troubleshooting

### Issue: "Docker daemon is not running"

**Solution:**
```bash
# Start Docker
docker start

# On Linux, may need to use sudo
sudo systemctl start docker
```

### Issue: "Connection refused" when starting TUI

**Check if server is running:**
```bash
curl http://localhost:8000/health
```

**If not running:**
- Terminal 1 should have `make dev-server` running
- Check for error messages in Terminal 1

### Issue: "No module named 'openai'" or other import errors

**Reinstall dependencies:**
```bash
uv pip install -e ".[all]"
```

### Issue: Agent is very slow or timing out

**Possible causes:**
1. **Large repository** - First time operations are slow (indexing)
2. **Slow LLM** - Local models or free APIs are slower
3. **Network issues** - Check internet connection
4. **Container startup** - Docker image pull takes time first run

**Solutions:**
```bash
# Increase timeout in .env
CONTAINER_TIMEOUT=600  # 10 minutes instead of 5

# Use faster LLM provider
# Switch from Claude to GPT-4, or local to OpenAI

# Check container logs
docker logs cognition-<session-id>
```

### Issue: "opencode-agent:py" image not found

**Rebuild the image:**
```bash
make build-agent-image
```

**Verify it exists:**
```bash
docker images | grep opencode-agent
```

### Issue: Permission denied when accessing files

**Ensure Docker user has permissions:**
```bash
# On Linux
sudo usermod -aG docker $USER
newgrp docker

# Restart Docker daemon
docker ps  # Should work without sudo now
```

### Issue: Out of disk space

**Clean up unused containers/images:**
```bash
# Remove stopped containers
docker container prune

# Remove unused images
docker image prune

# See disk usage
docker system df
```

### Issue: Cannot find Python modules in container

**Ensure dependencies are installed in image:**
```bash
docker build -t opencode-agent:py -f docker/Dockerfile.agent .
```

---

## Performance Tips

### 1. Use Faster LLM Provider

- **Fastest**: OpenAI GPT-4 (~0.5s per request)
- **Fast**: OpenAI GPT-3.5, Anthropic Claude 3 Haiku (~1s)
- **Medium**: Anthropic Claude 3 Opus (~3s)
- **Slow**: Local Ollama, vLLM (~5-30s)

### 2. Enable Network Mode for Large Repos

```
> /create --network ON
```

Allows `pip install` and other network operations.

### 3. Work on Smaller Sections

Instead of:
```
> Refactor entire codebase
```

Try:
```
> Search for routes in routes/
> Refactor src/auth/login.py
> Run tests for auth module only: pytest tests/auth -v
```

### 4. Reuse Sessions

Don't create new session for each task:
```
✓ Good: Create session, then multiple queries
✗ Bad: Create session, do one thing, quit, repeat
```

---

## Environment Variables Reference

```bash
# Server
HOST=0.0.0.0                          # Server bind address
PORT=8000                              # Server port
LOG_LEVEL=info                         # Logging level
DEBUG=false                            # Debug mode

# LLM Provider Selection
LLM_PROVIDER=openai                    # openai, anthropic, bedrock, openai_compatible

# OpenAI
OPENAI_API_KEY=sk-...                  # OpenAI API key
OPENAI_API_BASE=                       # Custom base URL (for compatible APIs)

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...           # Anthropic API key

# AWS Bedrock
USE_BEDROCK_IAM_ROLE=false             # Use IAM role instead of keys
AWS_ACCESS_KEY_ID=AKIA...              # AWS access key (if not using IAM role)
AWS_SECRET_ACCESS_KEY=...              # AWS secret key
AWS_SESSION_TOKEN=                     # Optional session token
AWS_PROFILE=default                    # AWS profile name
AWS_REGION=us-east-1                   # AWS region
BEDROCK_MODEL_ID=anthropic.claude-3... # Bedrock model ID

# Default Model
DEFAULT_MODEL=gpt-4-turbo-preview      # Default model for OpenAI/Anthropic

# Container
DOCKER_IMAGE=opencode-agent:py         # Docker image name
CONTAINER_TIMEOUT=300                  # Timeout in seconds
CONTAINER_MEMORY_LIMIT=2g              # Memory limit
CONTAINER_CPU_LIMIT=1.0                # CPU limit

# Workspace
WORKSPACE_ROOT=./workspaces            # Root directory for workspaces
MAX_SESSIONS=100                       # Max concurrent sessions

# Projects and Persistence
MAX_PROJECTS=1000                      # Maximum number of projects
PROJECT_CLEANUP_ENABLED=true           # Enable auto-cleanup
PROJECT_CLEANUP_AFTER_DAYS=30          # Delete after N days inactive
PROJECT_CLEANUP_WARNING_DAYS=3         # Warn N days before deletion
PROJECT_CLEANUP_CHECK_INTERVAL=86400   # Check interval in seconds
MEMORY_SNAPSHOT_ENABLED=true           # Enable memory snapshots
MEMORY_SNAPSHOT_INTERVAL=300           # Snapshot interval (seconds)
CONTAINER_STOP_ON_DISCONNECT=true      # Stop container on disconnect
CONTAINER_RECREATE_ON_RECONNECT=true   # Create fresh container on reconnect

# Backend Routes (Hybrid Memory Strategy)
AGENT_BACKEND_ROUTES='{ "/workspace/": {"type": "filesystem"}, "/memories/hot/": {"type": "store"}, "/memories/persistent/": {"type": "filesystem"}, "/tmp/": {"type": "state"} }'

# Observability
OTEL_EXPORTER_OTLP_ENDPOINT=           # OpenTelemetry endpoint
OTEL_SERVICE_NAME=cognition            # Service name for traces
```

---

## Next Steps

1. **Run your first session** following the examples above
2. **Try different LLM providers** to find what works for you
3. **Experiment with natural language** - the agent understands context
4. **Check out test cases** in `tests/` to understand capabilities
5. **Read the architecture** in `docs/architecture.md` for technical details

---

## Getting Help

### Check Logs

**Server logs** (Terminal 1):
```
INFO: Session created: xyz123
DEBUG: Tool called: read_file
ERROR: Container timeout after 300s
```

**Health check:**
```bash
curl http://localhost:8000/health
```

**Test specific component:**
```bash
uv run pytest tests/test_protocol.py -v
```

### Common Commands

```bash
# See all available tasks
make help

# Type checking
make typecheck

# Format code
make format

# Run full test suite
make test

# Clean up workspace
rm -rf workspaces/
```

---

## Support

- **Issues**: GitHub Issues on the repository
- **Discussions**: GitHub Discussions for feature requests
- **Logs**: Check Terminal 1 (server) for error messages

Enjoy using Cognition! 🚀
