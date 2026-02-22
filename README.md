# Cognition

> Secure execution, durable state, and audit trails for AI agents.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/release/python-3110/)
[![Status: Beta](https://img.shields.io/badge/Status-Beta-green.svg)](#)

Cognition is the open-source **Agent Substrate** — a hardened runtime engine that handles the critical infrastructure AI platforms need, but shouldn't have to build. Execution safety, durable persistence, and compliance-ready auditability come out of the box so you can focus on building your domain logic.

## The Platform Paradox

We are in the midst of a platform shift. Every industry—from Cybersecurity to BioTech—is rushing to build "AI Agents" into their core workflows. 

But building a production-grade Agent Platform requires solving three incredibly hard infrastructure problems that have nothing to do with the AI model itself:

1.  **Isolation (Execution):** How do you let an AI run code or tools safely without endangering your production infrastructure?
2.  **State (Persistence):** How do you ensure an investigation or workflow survives server restarts and remains resumable for weeks?
3.  **Trust (Auditability):** How do you prove to a regulator or legal team *exactly* what data the AI accessed and why it made a decision?

Most teams waste months building this scaffolding. **Cognition is that scaffolding.**

## Core Primitives

Cognition provides four fundamental primitives that you compose to build your platform:

*   🏗️ **The Cell (Execution)**: Pluggable sandbox backends (Local, Docker, Cloud) with secure command execution. No `shell=True`—commands parsed with `shlex` for safety.
*   🧬 **The Thread (State)**: Unified StorageBackend protocol supporting SQLite (dev) and PostgreSQL (production). Every step checkpointed; survives crashes and restarts.
*   🔍 **The Trace (Audit)**: Composable observability with OpenTelemetry and MLflow. Toggle backends via config—run OTel only, MLflow only, both, or neither.
*   🔌 **The Plug (Extensibility)**: Five-tier customization from `AGENTS.md` files to Python middleware, plus session scoping for multi-tenant isolation.

## Quick Start

### 1. Install Cognition

> **Note:** Cognition is currently in Beta. Install from GitHub while we prepare the PyPI release.

```bash
# Install from GitHub (recommended during Beta)
pip install git+https://github.com/CognicellAI/Cognition.git

# Or with uv
uv pip install git+https://github.com/CognicellAI/Cognition.git

# With specific LLM provider support
pip install "git+https://github.com/CognicellAI/Cognition.git#egg=cognition[openai]"
# or
pip install "git+https://github.com/CognicellAI/Cognition.git#egg=cognition[all]"
```

### 2. Configure Your Environment

**Basic Setup:**
```bash
# Create config directory
mkdir -p .cognition

# Create config file
cat > .cognition/config.yaml << 'EOF'
server:
  host: "127.0.0.1"
  port: 8000
  log_level: "info"

llm:
  provider: "openai"
  model: "gpt-4o"
  temperature: 0.7

# Enable session scoping for multi-tenancy
scoping:
  enabled: true
  keys: ["user", "project"]

# Configure observability
observability:
  otel_enabled: true
  mlflow_enabled: false
  metrics_port: 9090

# Choose persistence backend
persistence:
  backend: "sqlite"
  uri: ".cognition/state.db"
EOF
```

### 3. Start the Server
```bash
# Set your API key
export OPENAI_API_KEY="sk-..."

# Start the server
cognition-server
# Or: uv run python -m server.app.main
```

### 4. Create a Multi-Tenant Session
```bash
# Create a session with scoping headers
curl -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -H "X-Cognition-Scope-User: alice" \
  -H "X-Cognition-Scope-Project: security-review" \
  -d '{"title": "Security Investigation"}'
```

### 5. Stream a Response (SSE)
```bash
curl -N -X POST http://localhost:8000/sessions/{id}/messages \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -H "X-Cognition-Scope-User: alice" \
  -H "X-Cognition-Scope-Project: security-review" \
  -d '{"content": "Analyze the logs in ./data/incident-404"}'
```

### 6. Run Database Migrations (Production)
```bash
# For PostgreSQL deployments
cognition db upgrade
```

## Architecture

Cognition is a batteries-included agent backend following a strict 7-layer architecture. Define your agent; get everything else automatically.

```mermaid
graph TD
    subgraph "Your Application (The UX)"
        UI[Custom Dashboard / IDE / CLI]
        API_GW[Your API Gateway]
    end

    subgraph "The Substrate (Cognition Engine)"
        API[REST API / SSE Stream]
        
        subgraph "Layer 7: Observability"
            OTel[OpenTelemetry Traces]
            MLflow[MLflow Experiments]
            Prom[Prometheus Metrics]
        end
        
        subgraph "Layer 2-3: Storage & Execution"
            Storage[StorageBackend Protocol<br/>SQLite | PostgreSQL]
            Exec[ExecutionBackend Protocol<br/>Local | Docker | Cloud]
        end
        
        subgraph "Layer 4-6: Agent Runtime"
            Router[AgentRuntime Protocol]
            Circuit[Circuit Breaker]
            Scope[Session Scoping]
        end
    end

    UI --> API_GW
    API_GW --> API
    API --> Scope
    Scope --> Router
    Router --> Circuit
    Circuit --> Storage
    Router --> Exec
    Router -.-> OTel
    Router -.-> MLflow
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
| **Skills** | `SKILL.md` files | No Code | Reusable runbooks (e.g., "how to deploy") |
| **Subagents** | YAML Config | Config | Delegated specialists (e.g., "security-expert") |
| **Tools** | Python Functions | Code | Proprietary API integrations |
| **Middleware** | Python Classes | Code | Approval gates, custom telemetry |

## Blueprints

The CLI is just one example of what you can build on Cognition. See our [Blueprints](./docs/README.md#blueprints) for reference architectures:

*   **[Cognition CLI](./docs/blueprints/cognition-cli.md)**: A high-fidelity terminal assistant.
*   **[BreachLens](./docs/blueprints/cyber-investigation.md)**: Security analysis for cybersecurity investigations.
*   **[GeneSmith](./docs/blueprints/genesmith.md)**: Secure biological foundry for protein design.
*   **[DataLens](./docs/blueprints/data-analyst.md)**: Headless data science for sensitive datasets.
*   **[StarKeep](./docs/blueprints/starkeep.md)**: SpaceOps administrator for satellite repair.

## Production Features

Cognition is built for production deployments with comprehensive table-stakes, robustness, and full-vision features:

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
| **MLflow Evaluation** | ✅ | Offline evaluation and prompt registry |

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
- ✅ 223+ unit tests passing
- ✅ 40/41 E2E tests passing (1 MLflow async issue - upstream)
- ✅ Code coverage for P0/P1 features

## Documentation

*   📖 **[Core Concepts](./docs/README.md)**: Cells, Threads, Traces, and Plugs.
*   🛠️ **[Extending Agents](./docs/guides/extending-agents.md)**: How to add memory, skills, and tools.
*   ⚙️ **[Configuration Reference](./docs/guides/configuration.md)**: YAML and Environment variable details.
*   🚀 **[Deployment Guide](./docs/guides/deployment.md)**: Running in Docker and Kubernetes.

## License

MIT © [CognicellAI](LICENSE)
