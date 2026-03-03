# Getting Started

This guide walks you from zero to a running Cognition instance with an active session and a streaming response. It covers both paths: Docker Compose (no local Python required) and a direct Python install.

---

## Prerequisites

**Docker Compose path:** Docker Desktop or Docker Engine + Docker Compose v2.

**Python path:** Python 3.11+, `pip` or `uv`.

You will also need an LLM provider API key. Examples in this guide use OpenAI, but Cognition also supports AWS Bedrock, Ollama (local), and any OpenAI-compatible endpoint.

---

## Option A — Docker Compose

The fastest path. Starts Cognition with SQLite persistence. No local Python install required.

### 1. Clone and configure

```bash
git clone https://github.com/CognicellAI/Cognition.git
cd Cognition

# Copy the example env file
cp .env.example .env
```

Edit `.env` and set your API key:

```bash
OPENAI_API_KEY=sk-...
COGNITION_LLM_PROVIDER=openai
COGNITION_LLM_MODEL=gpt-4o
```

### 2. Start the server

```bash
docker-compose up -d cognition
```

This starts only the Cognition service. To start the full observability stack (Postgres, MLflow, Prometheus, Grafana, OTel Collector, Loki, Promtail) see the [Deployment guide](./deployment.md).

### 3. Verify it's running

```bash
curl -s http://localhost:8000/health | jq .
```

Expected output:
```json
{
  "status": "ok",
  "version": "0.1.0",
  "active_sessions": 0,
  "circuit_breakers": {},
  "timestamp": "2026-03-02T12:00:00Z"
}
```

---

## Option B — Python Install

### 1. Install

```bash
# From GitHub (includes OpenAI provider)
pip install "git+https://github.com/CognicellAI/Cognition.git#egg=cognition[openai]"

# Or with uv
uv add "git+https://github.com/CognicellAI/Cognition.git#egg=cognition[openai]"
```

For AWS Bedrock add `[bedrock]`; for local development add `[dev,test]`.

### 2. Configure

```bash
export OPENAI_API_KEY="sk-..."
export COGNITION_LLM_PROVIDER=openai
export COGNITION_LLM_MODEL=gpt-4o
```

Or create a `.env` file in your working directory (Cognition auto-loads it):

```bash
OPENAI_API_KEY=sk-...
COGNITION_LLM_PROVIDER=openai
COGNITION_LLM_MODEL=gpt-4o
```

### 3. Start the server

```bash
cognition-server
```

The server starts on `http://localhost:8000` by default.

---

## Your First Session

With the server running, create a session and send a message:

```bash
# Create a session
SESSION=$(curl -s -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"title": "My first session"}' | jq -r .id)

echo "Session ID: $SESSION"
```

```bash
# Send a message — streams tokens via SSE
curl -N -X POST "http://localhost:8000/sessions/$SESSION/messages" \
  -H "Content-Type: application/json" \
  -d '{"content": "What files are in the current directory?"}'
```

You will see a stream of SSE events:

```
event: status
data: {"status": "thinking"}

event: token
data: {"content": "I"}

event: token
data: {"content": " can"}

event: tool_call
data: {"name": "bash", "args": {"command": "ls -la"}, "id": "call_abc123"}

event: tool_result
data: {"tool_call_id": "call_abc123", "output": "total 24\ndrwxr-xr-x ...", "exit_code": 0}

event: token
data: {"content": "Here are the files..."}

event: usage
data: {"input_tokens": 45, "output_tokens": 120, "estimated_cost": 0.0008}

event: done
data: {"assistant_data": {...}}
```

---

## Using the CLI Client

Cognition ships with an interactive REPL built on `prompt_toolkit` and `rich`.

### Start the client

```bash
cognition-cli
```

The client auto-starts a server if one is not already running, creates a session, and drops into the interactive shell.

### Interactive Shell

The REPL shows a header with the active workspace and session ID, and a footer with the current model, token counts, and estimated cost.

**Slash commands:**

| Command | Description |
|---|---|
| `/help` | List available commands |
| `/session` | Show current session details |
| `/model <name>` | Switch the active model for this session |
| `/status` | Show server health and circuit breaker state |
| `/clear` | Clear the screen |
| `/exit` | Exit the REPL |

### Single-Message Mode

For scripting, pass a message directly:

```bash
cognition-cli --message "Summarize main.py"
```

Piped input also works:

```bash
cat error.log | cognition-cli
```

---

## List Available Agents

```bash
curl http://localhost:8000/agents
```

Cognition ships with two built-in agents:

| Name | Mode | Description |
|---|---|---|
| `default` | primary | Full-access coding agent; all tools enabled |
| `readonly` | primary | Analysis-only; write and execute tools disabled |

Create a session with a specific agent:

```bash
curl -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"agent_name": "readonly", "title": "Code review"}'
```

---

## Switching LLM Providers

### AWS Bedrock

```bash
export AWS_REGION=us-east-1
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export COGNITION_LLM_PROVIDER=bedrock
export COGNITION_LLM_MODEL=anthropic.claude-3-sonnet-20240229-v1:0
```

### Ollama (local)

```bash
export COGNITION_LLM_PROVIDER=ollama
export COGNITION_OLLAMA_MODEL=llama3.2
export COGNITION_OLLAMA_BASE_URL=http://localhost:11434
```

### OpenAI-compatible (OpenRouter, vLLM, etc.)

```bash
export COGNITION_LLM_PROVIDER=openai_compatible
export COGNITION_OPENAI_COMPATIBLE_BASE_URL=https://openrouter.ai/api/v1
export COGNITION_OPENAI_COMPATIBLE_API_KEY=sk-or-...
export COGNITION_LLM_MODEL=google/gemini-pro
```

---

## Project Configuration

Drop a `.cognition/config.yaml` in your project directory to configure the agent for that project:

```yaml
# .cognition/config.yaml
llm:
  provider: openai
  model: gpt-4o
  temperature: 0.5

agent:
  memory:
    - "AGENTS.md"       # Project-specific rules injected into system prompt
  skills:
    - ".cognition/skills/"
```

Place an `AGENTS.md` in your project root to inject project-specific context:

```markdown
# My Project

This is a Django REST API. Use Python 3.11+. Follow PEP 8.
Tests are in tests/ and run with pytest. The database is PostgreSQL.
```

---

## Next Steps

- [Configuration](./configuration.md) — Full reference for all settings
- [Extending Agents](./extending-agents.md) — Add custom tools, skills, and subagents
- [Deployment](./deployment.md) — Run with PostgreSQL, Docker sandbox, and full observability
- [API Reference](./api-reference.md) — Every endpoint and SSE event type
