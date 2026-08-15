# Architecture Decision Records

**Status:** Active index  
**Last updated:** 2026-08-03

Architecture Decision Records (ADRs) capture decisions that should survive the
implementation that first introduced them. Use an ADR when changing:

- Cognition versus builder responsibility
- A public protocol or compatibility promise
- The authoritative record for runtime state
- A deployable or trust boundary
- Scope propagation or isolation semantics
- A persistence, extension, or sandbox abstraction
- A rule that constrains several future features

Routine implementation details belong in code and tests. Delivery sequencing
belongs in `ROADMAP.md`.

## Index

| ADR | Status | Decision |
|---|---|---|
| [ADR-0001](0001-v013-exact-scope-agent-runtime-boundary.md) | Accepted | v0.13 uses an exact-scope Agent runtime boundary with explicit Agent provisioning, pinned run manifests, and fail-closed production execution. |
| [ADR-0002](0002-curated-opentelemetry-agent-tracing.md) | Accepted | Agent runs use a curated OpenTelemetry trace rooted at the durable attempt, with automatic framework instrumentation, routine-span filtering, run/session correlation, and canonical OTLP delivery. |
| [ADR-0003](0003-agent-owned-capability-revisions.md) | Accepted | Agent definitions own capability selection and publish immutable revisions that runs pin at startup. |
| [ADR-0004](0004-mcp-transport-authentication-and-builder-authorization.md) | Accepted | MCP transport supports anonymous, standard MCP OAuth, workload token exchange, and environment-backed bearer authentication while builders retain deployment policy and live Agent authorization. |
| [ADR-0005](0005-durable-state-placement-and-backend-composition.md) | Accepted | Database manifests and S3-compatible bodies form the recommended distributed topology; builders may select local backends without an environment classifier. |

Use [the ADR template](0000-template.md) and assign the next four-digit number.
Files use `NNNN-short-decision-title.md`.

## Lifecycle

1. **Proposed:** The trade-off and migration are under review.
2. **Accepted:** The decision governs implementation.
3. **Superseded:** A newer ADR explicitly replaces it.
4. **Rejected:** The option was considered and deliberately not selected.

Accepted ADRs must link the tests or evidence that demonstrate conformance.
Superseded records remain in the index so architectural history is not rewritten.

## Related governance

- [Architecture governance](../09-governance-and-evolution.md)
- [Code-derived risks](../10-code-derived-risks.md)
- [Architecture overview](../index.md)
