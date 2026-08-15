# Cognition Documentation

Cognition is a **headless agent orchestration backend**. Define your agent and
get REST API, streaming, persistence, sandboxing, multi-user scoping, and
observability automatically.

This site is organized around four documentation surfaces:

- **Architecture** records code-derived C4 views, runtime flows, operational
  constraints, and durable decisions.
- **Concepts** explain individual subsystems.
- **Guides** show how to perform concrete tasks.
- **Blueprints** describe reference architectures for products built on
  Cognition.

If you want a course sequence rather than reference material, use
[Learn agent development](/learn/).

## Start Here

- [Getting Started](guides/getting-started.md): install, configure, and send
  your first message.
- [Core vs App Layer](guides/core-vs-app-layer.md): understand what Cognition
  owns versus what your product owns.
- [Architecture](architecture/index.md): explore code-derived C4 diagrams,
  component boundaries, runtime flows, deployment topology, and decisions.
- [Extending Agents](guides/extending-agents.md): add memory, skills, tools,
  subagents, middleware, MCP servers, and custom providers.
- [A2A Builder Guide](guides/a2a.md): publish, describe, and securely expose
  agents through A2A 1.0.
- [AWS Lambda MicroVM Sandbox Setup](concepts/sandboxes/aws-lambda-microvm/setup.md):
  configure AWS-native MicroVM sandbox isolation for agent commands.

## Concepts

| Document | Description |
|---|---|
| [Architecture](architecture/index.md) | Code-derived C4 views, runtime flows, deployment topology, risks, and decisions |
| [Sessions & Messages](concepts/sessions-and-messages.md) | Session lifecycle, message persistence, SSE streaming, and reconnection |
| [Agent Runtime](concepts/agent-runtime.md) | Agent runtime protocol, definitions, A2A exposure, and multi-agent registry |
| [A2A in Cognition](concepts/a2a/index.md) | Agent Cards, message Parts, tasks, streaming, artifacts, security, and scoping |
| [Storage & Execution](concepts/storage-and-execution.md) | Storage and execution backend protocols and implementations |
| [Sandboxes](concepts/sandboxes/index.md) | Local, Docker, Kubernetes, and AWS Lambda MicroVM execution backends |
| [Kubernetes Sandbox](concepts/sandboxes/kubernetes/index.md) | K8s-native sandbox isolation using the agent-sandbox CRD and controller |
| [AWS Lambda MicroVM Sandbox](concepts/sandboxes/aws-lambda-microvm/index.md) | AWS-native MicroVM sandbox isolation, profiles, IAM roles, networking, and runtime protocol |
| [Observability](concepts/observability.md) | OpenTelemetry traces, Prometheus metrics, and MLflow experiment tracking |
| [Security](concepts/security.md) | Scoping, sandbox isolation, tool security, MCP policy, A2A boundaries, rate limiting, and CORS |

## Guides

| Document | Description |
|---|---|
| [Getting Started](guides/getting-started.md) | Install, configure, and send your first message |
| [Core vs App Layer](guides/core-vs-app-layer.md) | Builder responsibilities: what belongs in Cognition versus your product |
| [Configuration](guides/configuration.md) | YAML keys and environment variables |
| [Extending Agents](guides/extending-agents.md) | Add memory, skills, tools, subagents, middleware, MCP servers, A2A exposure, and custom providers |
| [A2A Builder Guide](guides/a2a.md) | Configure Agent Cards, public skills, media modes, message Parts, authentication discovery, and scope isolation |
| [Deployment](guides/deployment.md) | Docker Compose, PostgreSQL, migrations, and production hardening |
| [API Reference](guides/api-reference.md) | REST endpoints, SSE events, artifacts, A2A protocol, capabilities, and scoping headers |
| [Release Checklist](guides/release-checklist.md) | Release process for Cognition versions |

## Blueprints

| Blueprint | Domain |
|---|---|
| [Cognition CLI](blueprints/cognition-cli.md) | Terminal assistant over the Cognition API |
| [BreachLens](blueprints/cyber-investigation.md) | Cybersecurity SOC investigation and triage |
| [GeneSmith](blueprints/genesmith.md) | Secure biological foundry for protein design |
| [DataLens](blueprints/data-analyst.md) | Headless data science on sensitive datasets |
| [StarKeep](blueprints/starkeep.md) | SpaceOps administrator for satellite repair |
