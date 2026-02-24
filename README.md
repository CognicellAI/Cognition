# Cognition

> A batteries-included backend for building production AI agent platforms. Define your agent; get REST API, streaming, persistence, sandboxing, and observability automatically.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/release/python-3110/)
[![Status: Beta](https://img.shields.io/badge/Status-Beta-green.svg)](#)

Cognition is a headless backend that handles the hard infrastructure problems of production AI agents: sandboxed execution, durable session state, and full observability. Built on [Deep Agents](https://github.com/CognicellAI/Cognition) and [LangGraph](https://langchain-ai.github.io/langgraph/), it provides a declarative model where you define agents and tools, and Cognition provides the runtime, API, and operational infrastructure.

## Quick Start

### Option A — Docker Compose (fastest, no install required)

```bash
# Clone and start
git clone https://github.com/CognicellAI/Cognition.git
cd Cognition

# Copy environment config and add your API key
cp .env.example .env
# Edit .env to add: OPENAI_API_KEY=sk-...

# Start the server
docker-compose up -d

# Verify it's running
curl -s http://localhost:8000/health | jq .

# Create a session
SESSION=$(curl -s -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"title": "My first session"}' | jq -r .id)

# Send a message (streams via Server-Sent Events)
curl -N -X POST "http://localhost:8000/sessions/$SESSION/messages" \
  -H "Content-Type: application/json" \
  -d '{"content": "List the files in the workspace."}'
```

### Option B — pip install (embed in an existing project)

```bash
# Install from GitHub
pip install "git+https://github.com/CognicellAI/Cognition.git#egg=cognition[openai]"

# Set your API key
export OPENAI_API_KEY="sk-..."

# Start the server
cognition-server
```

## What Cognition Provides

- **Sandboxed Execution** — Pluggable backends: local subprocess, Docker container, or cloud. No `shell=True`; commands parsed with `shlex` for safety.
- **Durable Sessions** — StorageBackend protocol: SQLite (dev) or PostgreSQL (prod). Every agent step checkpointed; survives crashes and restarts.
- **Full Observability** — OpenTelemetry traces → MLflow. Toggle independently; zero-config when disabled.
- **Multi-Tenant Isolation** — Session scoping via `X-Cognition-Scope-*` headers. Rate limiting, CORS, and circuit breaker built in.
- **Multi-Agent Registry** — Built-in agents (`default`, `readonly`) plus user-defined agents in `.cognition/agents/`. Session-agent binding via `agent_name`.

## The Problem Cognition Solves

Building a production AI agent platform requires solving infrastructure problems that have nothing to do with the model itself:

- **Isolation:** How do you let an agent run code or shell commands without endangering your infrastructure?
- **State:** How do you ensure a workflow survives server restarts and remains resumable across days?
- **Auditability:** How do you prove to an auditor exactly what the agent accessed and what logic it used?

Cognition solves these three problems so you don't have to.

## Architecture

Cognition is a batteries-included agent backend following a strict 7-layer architecture. Define your agent; get everything else automatically.

```mermaid
graph TD
    subgraph "Your Application"
        UI[Custom Dashboard / IDE / CLI]
        API_GW[Your API Gateway]
    end

    subgraph "Cognition Engine"
        API[REST API / SSE Stream]
        
        subgraph "Agent Runtime"
            Router[AgentRuntime Protocol]
            Circuit[Circuit Breaker]
            Scope[Session Scoping]
        end
        
        subgraph "Storage & Execution"
            Storage[StorageBackend Protocol<br/>SQLite | PostgreSQL]
            Exec[ExecutionBackend Protocol<br/>Local | Docker | Cloud]
        end
        
        subgraph "Observability"
            OTel[OpenTelemetry Traces]
            MLflow[MLflow Experiments]
            Prom[Prometheus Metrics]
        end
    end

    UI --> API_GW
    API_GW --> API
    API --> Scope
    Scope --> Router
    Router --> Circuit
    Circuit --> Storage
    Router --> Exec
    Router --> OTel
    Router --> MLflow
    Router --> Prom
```

### Key Architectural Patterns

**StorageBackend Protocol:** Unified interface for sessions, messages, and checkpoints. Swappable SQLite ↔ PostgreSQL without code changes.

**ExecutionBackend Protocol:** Cognition-owned abstraction over Deep Agents. Local subprocess, Docker containers, or cloud functions—all implement the same interface.

**AgentRuntime Protocol:** Clean boundary between Cognition and Deep Agents. Enables framework swapping without rewriting business logic.

**Session Scoping:** Multi-dimensional isolation via HTTP headers. Filter sessions by user, project, team, or custom dimensions.

## Extend Your Agent

Cognition uses a "Convention over Configuration" model. Most customizations require zero code.

| Level | Mechanism | Effort | Example |
|---|---|---|---|
| **Memory** | `AGENTS.md` | No Code | Project-specific rules & style |
| **Skills** | `.cognition/skills/` | No Code | Reusable runbooks (e.g., "how to deploy") |
| **Agents** | `.cognition/agents/` | Config | Delegated specialists (e.g., "security-expert") |
| **Tools** | Python Functions | Code | Proprietary API integrations |
| **Middleware** | Python Classes | Code | Approval gates, custom telemetry |

## Multi-Agent Registry

Cognition ships with built-in agents and loads user-defined agents from `.cognition/agents/`:

| Agent | Mode | Description |
|---|---|---|
| `default` | primary | Full-access coding agent with all tools enabled |
| `readonly` | primary | Analysis-only agent; write and execute tools disabled |
| Your agents | primary or subagent | Defined in `.cognition/agents/*.md` or `*.yaml` |

**Agent Modes:**
- `primary` — Can be used as the main agent for a session
- `subagent` — Can only be invoked by other agents via the `task` tool
- `all` — Can function as both

Create a session with a specific agent:

```bash
curl -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"agent_name": "readonly", "title": "Code Review"}'
```

List all available agents:

```bash
curl http://localhost:8000/agents
```

## Blueprints

The CLI is just one example of what you can build on Cognition. See our [Blueprints](./docs/README.md#blueprints) for reference architectures:

*   **[Cognition CLI](./docs/blueprints/cognition-cli.md)**: A high-fidelity terminal assistant.
*   **[BreachLens](./docs/blueprints/cyber-investigation.md)**: Security analysis for cybersecurity investigations.
*   **[GeneSmith](./docs/blueprints/genesmith.md)**: Secure biological foundry for protein design.
*   **[DataLens](./docs/blueprints/data-analyst.md)**: Headless data science for sensitive datasets.
*   **[StarKeep](./docs/blueprints/starkeep.md)**: SpaceOps administrator for satellite repair.

## Production Features

Cognition is built for production deployments:

| Feature | Status | Description |
|---------|--------|-------------|
| **Message Persistence** | ✅ | SQLite/PostgreSQL message storage with pagination |
| **Session Scoping** | ✅ | Multi-tenant isolation via HTTP headers |
| **Rate Limiting** | ✅ | Token bucket with scope-aware keys |
| **Abort** | ✅ | Cancel streaming tasks gracefully |
| **Observability** | ✅ | Toggle OTel/MLflow independently |
| **StorageBackend** | ✅ | Unified protocol: SQLite ↔ PostgreSQL |
| **Docker Sandbox** | ✅ | Container-per-session with resource limits |
| **Alembic Migrations** | ✅ | Database schema versioning |
| **Circuit Breaker** | ✅ | Resilient LLM provider failover |
| **CORS** | ✅ | Cross-origin web app support |
| **MLflow Evaluation** | ✅ | Offline evaluation pipeline with 3 built-in scorers |
| **Multi-Agent Registry** | ✅ | Built-in + user-defined agents; `GET /agents` endpoint |

## Testing

Cognition includes comprehensive test coverage:

```bash
# Run unit tests
uv run pytest tests/unit/ -v

# Run E2E tests
uv run pytest tests/e2e/ -v

# Run specific test
uv run pytest tests/unit/test_message_store.py -v
```

**Current Status:**
- ✅ 308+ unit tests passing
- ✅ 29/29 E2E scenario tests passing
- ✅ Code coverage for P0/P1/P2/P3 features

## Documentation

*   📖 **[Core Concepts](./docs/README.md)**: Cells, Threads, Traces, and Plugs.
*   🛠️ **[Extending Agents](./docs/guides/extending-agents.md)**: How to add memory, skills, and tools.
*   ⚙️ **[Configuration Reference](./docs/guides/configuration.md)**: YAML and Environment variable details.
*   🚀 **[Deployment Guide](./docs/guides/deployment.md)**: Running in Docker and Kubernetes.

## Contributing

Bug reports, questions, and pull requests are welcome. Open an issue before submitting large changes. See the test suite for patterns: `uv run pytest tests/unit/ -v`.

## License

MIT © [CognicellAI](LICENSE)
