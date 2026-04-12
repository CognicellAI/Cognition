# Cognition Documentation

Cognition is a **headless agent orchestration backend**. Define your agent — get REST API, streaming, persistence, sandboxing, and observability automatically.

The documentation is organized into two sections: **Concepts** explain how Cognition works internally; **Guides** are task-oriented and tell you how to do specific things.

---

## Concepts

| Document | Description |
|---|---|
| [Architecture](./concepts/architecture.md) | The 7-layer architecture, dependency rules, and the batteries-included design |
| [Sessions & Messages](./concepts/sessions-and-messages.md) | Session lifecycle, message persistence, SSE streaming, and reconnection |
| [Agent Runtime](./concepts/agent-runtime.md) | AgentRuntime protocol, AgentDefinition model, and the multi-agent registry |
| [Storage & Execution](./concepts/storage-and-execution.md) | StorageBackend and ExecutionBackend protocols and their implementations |
| [Kubernetes Sandbox](./concepts/kubernetes-sandbox.md) | K8s-native sandbox isolation using the agent-sandbox CRD and controller |
| [Observability](./concepts/observability.md) | OpenTelemetry traces, Prometheus metrics, and MLflow experiment tracking |
| [Security](./concepts/security.md) | Session scoping, sandbox isolation, tool security, rate limiting, and CORS |

---

## Guides

| Document | Description |
|---|---|
| [Getting Started](./guides/getting-started.md) | Install, configure, and send your first message |
| [Core vs App Layer](./guides/core-vs-app-layer.md) | Builder responsibilities: what belongs in Cognition versus your product |
| [Configuration](./guides/configuration.md) | Complete reference for all YAML keys and environment variables |
| [Extending Agents](./guides/extending-agents.md) | Add memory, skills, tools, subagents, middleware, and custom LLM providers |
| [Deployment](./guides/deployment.md) | Docker Compose stack, PostgreSQL, Alembic migrations, and production hardening |
| [API Reference](./guides/api-reference.md) | Every REST endpoint, SSE event type, and scoping header |

---

## Blueprints

Reference architectures showing what you can build on Cognition:

| Blueprint | Domain |
|---|---|
| [Cognition CLI](./blueprints/cognition-cli.md) | Terminal assistant — a thin client over the Cognition API |
| [BreachLens](./blueprints/cyber-investigation.md) | Cybersecurity SOC investigation and triage |
| [GeneSmith](./blueprints/genesmith.md) | Secure biological foundry for protein design |
| [DataLens](./blueprints/data-analyst.md) | Headless data science on sensitive datasets |
| [StarKeep](./blueprints/starkeep.md) | SpaceOps administrator for satellite repair |
