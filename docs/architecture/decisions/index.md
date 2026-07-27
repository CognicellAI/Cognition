# Architecture Decision Records

**Status:** Active index  
**Last updated:** 2026-07-26

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
