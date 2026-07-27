# Proposals

**Status:** Design workspace  
**Audience:** Maintainers, deployment operators, and builders  
**Last updated:** 2026-07-26

Proposals describe shaped architectural directions as they move from design to
implementation. They are release-independent until accepted through the roadmap
and architecture decision process.

## Active proposals

| Proposal | Status | Purpose |
| --- | --- | --- |
| [Sandboxed skill package registry](sandboxed-skill-registry.md) | Draft | Store scope-bound skill packages in a configurable backend and materialize immutable revisions into isolated sandboxes |
| [Curated OpenTelemetry Agent tracing](curated-opentelemetry-tracing.md) | Implemented on v0.13 branch | Replace noisy, duplicative traces and estimated usage with a bounded Agent-run trace and provider-authoritative Usage Events |

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
