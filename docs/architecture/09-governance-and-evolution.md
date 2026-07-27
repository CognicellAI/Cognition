# Architecture Governance and Evolution

**Status:** Active maintenance contract  
**Code baseline:** `release/v0.13.0` (`890e1ad`)  
**Last verified:** 2026-07-25

This page defines how the architecture model stays useful after the initial
code-derived audit. It complements `ROADMAP.md`: the roadmap governs delivery;
this section governs boundaries, evidence, and decisions.

## Source-of-truth order

When sources disagree, use this order:

1. Executable code and persistence schemas
2. Tests that exercise observable behavior
3. Deployment manifests and package metadata
4. Accepted Architecture Decision Records (ADRs)
5. This architecture model
6. Concept and guide prose

An inconsistency is not resolved by editing only the lowest-ranked source. If
code violates an accepted decision, either correct the code or explicitly
replace the decision.

## Status vocabulary

| Status | Meaning |
| --- | --- |
| Current | Verified in the named code baseline |
| Optional | Implemented but activated only by configuration or extras |
| External | Required responsibility implemented outside Cognition |
| Constraint | Current behavior that designs and operators must account for |
| Proposed | Shaped direction without merged implementation |
| Superseded | Replaced by a newer documented decision or implementation |

Avoid “planned” architecture diagrams that look identical to implemented
diagrams. Put proposals in a decision record or roadmap entry and label them
clearly.

## Change trigger matrix

| Changed code or contract | Architecture pages to review | Required evidence |
| --- | --- | --- |
| `server/app/main.py`, dependency wiring, route registration | Server composition, runtime flows, deployment | Startup/shutdown test and route inventory |
| `server/app/api/`, SSE, A2A routes | System context, server composition, runtime flows | OpenAPI/capabilities diff and protocol tests |
| `AgentDefinition`, runtime resolver, graph factory, middleware | Agent runtime, runtime flows | Definition round-trip and runtime boundary tests |
| Task/run/event lifecycle or domain statuses | Agent runtime, state/configuration, runtime flows | State-transition, restart, cancellation, and replay tests |
| Storage protocols, schema, migrations, cleanup | State/configuration, deployment, risks | Clean install plus upgrade and isolation tests |
| ConfigRegistry, ConfigStore, dispatcher, cache | Agent runtime, state/configuration, runtime flows, risks | Cross-process invalidation and precedence tests |
| Sandbox protocol, packages, profiles, images | Execution/sandboxes, deployment, system context | Backend conformance and teardown tests |
| Scope extraction or propagation | Every C4 level, state/configuration, risks | Cross-scope negative tests for touched resources |
| Metrics, traces, logs, callbacks, health | Server composition, deployment, risks | Cardinality, raw-trace export, log redaction, dependency-failure tests |
| Docker, Compose, Helm, CI/release workflows | Container/deployment views | Image build, Helm render/lint, and topology checks |

## Architecture change workflow

1. **Classify the work.** Architectural changes require a complete ROADMAP entry
   and migration plan before implementation.
2. **Record a durable decision.** Create an ADR when changing ownership,
   authority, a public protocol, a persistence authority, a deployable boundary,
   or an extension model.
3. **Update the C4 model in the same change.** Static diagrams, dynamic flows,
   evidence tables, and current-risk status should agree with merged code.
4. **Prove the boundary.** Prefer tests at protocols/application-service
   boundaries over tests coupled to internal helper functions.
5. **Verify deployment effects.** Check startup, upgrade, multi-replica behavior,
   isolation, observability, and rollback when relevant.
6. **Stamp the baseline.** Update the version/commit and verification date on
   every affected architecture page.

The [decision index](decisions/index.md) defines ADR format and lifecycle. The
[code-derived risk register](10-code-derived-risks.md) records current gaps that
future changes must close or explicitly accept.

## Boundary rules

The code-derived model establishes these durable boundaries:

- The builder owns identity, authorization decisions, tenants, roles,
  entitlements, lifecycle orchestration, and public routing.
- Cognition carries builder-authorized effective scope and owns scoped Agent
  CRUD plus execution semantics.
- REST/SSE and A2A remain adapters over protocol-neutral task/run/event state.
- LangGraph checkpoints are authoritative conversation/runtime state; the
  message table is a rebuildable query projection.
- Sandbox selection changes execution placement without changing the Agent-facing
  backend contract.
- PostgreSQL shares durable state, but it does not automatically distribute
  process-local graphs, streams, rate limits, or sandbox handles.
- Observability signals and durable runtime events are related but distinct
  products: telemetry explains the system; runtime events are builder-visible
  lifecycle evidence.

Changing one of these rules requires an ADR, roadmap migration plan, and updated
C4/dynamic views.

## Documentation verification

For an architecture change, complete this check before merge:

- [ ] Every diagram element maps to an executable component or is labeled
      external/optional/proposed.
- [ ] Every relationship has a protocol or call direction visible in code.
- [ ] Authority and projection language matches storage contracts.
- [ ] Scope semantics describe current equality/inheritance behavior precisely.
- [ ] Process-local and durable behavior are not conflated.
- [ ] Deployment replicas and external dependencies match manifests.
- [ ] Code evidence paths and symbols still exist.
- [ ] Relative links resolve and `uv run mkdocs build --strict` passes.
- [ ] C4 and sequence diagrams render in the generated site.
- [ ] Risks closed by the change are marked resolved with evidence.
- [ ] All affected pages carry the new code baseline and verification date.

## Review cadence

Review this architecture set at each minor release and whenever a change trigger
in the matrix fires. At release freeze:

1. Compare route, settings, schema, package, and deployment inventories with the
   diagrams.
2. Re-run the risk register against code.
3. Confirm every accepted ADR still matches implementation.
4. Update the baseline commit only after validation uses the intended release
   commit.

## Code evidence

The governance rules reflect the code areas that form the current composition
and extension boundaries:

- `server/app/main.py`
- `server/app/api/`
- `server/app/agent/`
- `server/app/llm/deep_agent_service.py`
- `server/app/storage/`
- `server/app/execution/`
- `server/app/protocols/a2a/`
- `packages/langchain-*/`
- `deploy/`, `Dockerfile*`, `docker-compose.yml`
- `.github/workflows/`

## Related views

- [Architecture overview](index.md)
- [Code-derived risk register](10-code-derived-risks.md)
- [Architecture decisions](decisions/index.md)
