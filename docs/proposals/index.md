# Proposals

**Status:** Proposal and design history index
**Audience:** Maintainers, deployment operators, and builders  
**Last reviewed:** 2026-11-25

Proposals record design intent and alternatives. The [architecture](../architecture/index.md)
and [decision records](../architecture/decisions/index.md) describe the current
system and its governing decisions.

## Historical proposals

| Proposal | Status | Purpose |
| --- | --- | --- |
| [Optional A2UI v1.0 support for A2A Agents](optional-a2ui-v1-a2a-extension.md) | Implemented in v0.15.0 | Optional pinned A2UI Candidate support. See the [operator guide](../concepts/a2a/a2ui.md). |
| [v0.14.0 C4 Model: Agent Capabilities and Durable Storage](v0.14.0-deep-agents-skills-mcp-storage.md) | v0.14 design record | Agent-owned capabilities and durable storage. See [ADR-0003](../architecture/decisions/0003-agent-owned-capability-revisions.md) and [ADR-0005](../architecture/decisions/0005-durable-state-placement-and-backend-composition.md). |
| [v0.14.0 MCP Runtime and Authentication Contract](v0.14.0-mcp-runtime-contract.md) | v0.14 design record | Agent-owned MCP configuration and authentication. See [ADR-0004](../architecture/decisions/0004-mcp-transport-authentication-and-builder-authorization.md). |
| [Sandboxed skill package registry](sandboxed-skill-registry.md) | Superseded | Rejected name-based registry design retained as architectural history |
| [Curated OpenTelemetry Agent tracing](curated-opentelemetry-tracing.md) | Implemented | Curated traces and provider-authoritative usage. See [ADR-0002](../architecture/decisions/0002-curated-opentelemetry-agent-tracing.md). |

## Status meanings

| Status | Meaning |
| --- | --- |
| Draft | Open for design review; no implementation commitment |
| Accepted | Approved through governance and ready for roadmap scheduling |
| Implementing | Backed by an active roadmap item and implementation work |
| Implemented | Delivered and reflected in the code-derived architecture |
| Superseded | Replaced by another proposal or architecture decision |

A draft proposal must not be presented as current architecture. Once
implementation starts, follow the [architecture change workflow](../architecture/09-governance-and-evolution.md)
and create an Architecture Decision Record (ADR) when the change affects a
public protocol, persistence authority, execution boundary, or extension model.

## Related documentation

- [Code-derived architecture](../architecture/index.md)
- [Architecture decisions](../architecture/decisions/index.md)
- [Code-derived risks](../architecture/10-code-derived-risks.md)
- [Core versus application layer](../guides/core-vs-app-layer.md)
