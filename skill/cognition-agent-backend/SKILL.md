---
name: cognition-agent-backend
description: Use when helping a developer integrate, configure, extend, or deploy the Cognition AI agent backend. Covers installation, configuration, REST API usage, agent extensibility (tools, skills, middleware, subagents), and production deployment with Docker, PostgreSQL, and observability.
compatibility: "Python 3.11+, uv package manager"
---

# Cognition Agent Backend

Cognition is a batteries-included AI agent backend that provides secure execution, durable persistence, and audit trails for AI agents. It is built on LangGraph Deep Agents and exposes a FastAPI REST/SSE API.

## Core Capabilities

- **Secure Execution**: Local or Docker-based sandboxing with resource limits and network isolation
- **Persistence**: Durable session storage (SQLite/PostgreSQL) with full message history
- **Observability**: OpenTelemetry tracing, Prometheus metrics, and structured logging
- **Multi-Tenancy**: Optional scoping for user/project isolation
- **Extensibility**: Custom tools, skills, middleware, and subagents

## Instructions

### 1. Integration & Setup

Use this workflow to set up Cognition in a new or existing Python project.

1.  **Install Dependencies**:
    Cognition requires Python 3.11+ and `uv`.
    ```bash
    uv sync --extra openai --extra deploy  # Common production profile
    ```

2.  **Initialize Configuration**:
    Create the configuration structure.
    ```bash
    cognition init --project  # Creates .cognition/config.yaml
    ```

3.  **Configure Environment**:
    Set minimal environment variables in `.env`. See [Configuration Reference](references/configuration.md) for details.
    ```env
    COGNITION_LLM_PROVIDER=openai
    COGNITION_LLM_MODEL=gpt-4o
    OPENAI_API_KEY=sk-...
    ```

4.  **Verify Setup**:
    Run the health check to validate configuration and dependencies.
    ```bash
    cognition health
    ```

### 2. Development Workflow

Use this workflow for building and testing agents.

1.  **Start Server**:
    Run the development server with auto-reload.
    ```bash
    cognition serve --reload
    ```

2.  **Define Agent**:
    Edit `.cognition/agent.yaml` to define tools, skills, and behavior. See [Extension Points](references/extension-points.md).

3.  **Interact**:
    Use the REST API or client to start a session. See [API Reference](references/api-reference.md).
    ```bash
    curl -X POST http://localhost:8000/sessions -d '{"title": "Dev Session"}'
    ```

### 3. Production Deployment

Use this workflow for deploying to production environments.

1.  **Configure Persistence**:
    Switch to PostgreSQL for durable storage.
    ```env
    COGNITION_PERSISTENCE_BACKEND=postgres
    COGNITION_PERSISTENCE_URI=postgresql://user:pass@host/db
    ```

2.  **Enable Scoping**:
    Turn on multi-tenancy for security.
    ```env
    COGNITION_SCOPING_ENABLED=true
    COGNITION_SCOPE_KEYS=["user", "project"]
    ```

3.  **Deploy Stack**:
    Use Docker Compose or Kubernetes. See [Deployment Guide](references/deployment.md).

## Reference Index

- [Configuration Reference](references/configuration.md) - Environment variables and YAML options
- [API Reference](references/api-reference.md) - REST endpoints and SSE events
- [Extension Points](references/extension-points.md) - Tools, skills, middleware, subagents
- [Deployment Guide](references/deployment.md) - Docker, database, observability
- [Troubleshooting](references/troubleshooting.md) - Common errors and solutions
