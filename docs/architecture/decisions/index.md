# Architecture Decision Records

**Status:** Active index  
**Last updated:** 2026-07-25

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

- `ROADMAP.md`
- [Architecture overview](../../concepts/architecture.md)
- [Agent runtime](../../concepts/agent-runtime.md)
- [Configuration guide](../../guides/configuration.md)
