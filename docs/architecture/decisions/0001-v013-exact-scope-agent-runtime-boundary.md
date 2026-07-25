# ADR-0001: v0.13 Exact-Scope Agent Runtime Boundary

**Status:** Accepted  
**Date:** 2026-07-25  
**Deciders:** Cognition maintainers  
**Supersedes:** None  
**Related roadmap/issue:** `ROADMAP.md` — v0.13.0 multi-tenant Agent runtime boundary

## Context

Cognition is a backend runtime for builder-owned Agent applications. For
multi-tenant builders, the unsafe failure modes are not just obvious
cross-tenant reads; they also include default Agent fallback, inherited API
Agent definitions, process-lifetime caches, stale session sandboxes, callback
egress, and telemetry batches that disappear during long runs.

Before v0.13, several runtime paths still assumed convenient single-tenant or
developer-mode behavior:

- A deployment could supply platform default Agents.
- Session creation could implicitly bind an Agent.
- Some API-created Agent behavior could inherit broader scope.
- Runtime state and cache identity did not consistently include exact scope,
  Agent revision, dependency digests, and sandbox identity.
- Local or host execution paths could remain available unless carefully
  configured away.

Cognition must improve those boundaries without becoming a builder control
plane. Builders still own authentication, authorization, tenant UX, publishing,
credentials, billing, and policy decisions. Cognition receives trusted
`effective_scope` and enforces it as a runtime boundary.

## Decision

For v0.13, Cognition adopts an exact-scope Agent runtime boundary:

1. Cognition supplies no default Agents. A new project starts empty, and every
   session must name an explicitly builder-provisioned Agent.
2. API-created Agents resolve only at the complete trusted scope. Broader
   scope inheritance is not allowed for API Agents. Explicit shared file Agents
   may remain as read-only fallback definitions.
3. Agent definitions carry canonical `scope_key`, `revision`, and
   `definition_digest`. Reads expose revision/digest identity and ETags;
   replacement, patch, and deletion support conditional writes.
4. Runtime state access is exact-scope. Sessions, messages, runs, events,
   tasks, artifacts, checkpoints, cleanup, and deletion return not-found for
   wrong-scope identifiers.
5. Each run resolves the Agent and dependencies once, then persists a redacted,
   digest-pinned runtime manifest. Active runs do not observe later Agent or
   dependency updates.
6. Graph cache keys include the effective-scope fingerprint, Agent revision,
   runtime manifest digest, sandbox backend identity, model identity, and
   relevant runtime settings. Cached graphs do not own a sandbox backend; the
   current run supplies sandbox routing dynamically.
7. Production execution fails closed for unsupported host/local execution
   paths. Unsafe local execution, host tools, API Python tools, and local
   fallback require explicit development configuration.
8. Shared runtime caches are size/TTL bounded and expose safe metrics.
9. Per-message completion callbacks are denied unless the operator approves
   the exact HTTPS origin.
10. OTLP trace export requests are bounded by encoded byte size so long runs do
    not exceed common collector limits.

## Alternatives considered

### Keep defaults and inheritance with stricter checks

This would preserve smoother local demos, but defaults and broad Agent
inheritance create ambiguous ownership in multi-tenant deployments. It also
makes wrong-Agent execution look like success. v0.13 chooses explicit
provisioning and exact-scope API Agent resolution instead.

### Build a tenant control plane inside Cognition

Cognition could model tenants, users, roles, catalogs, credentials, and
publishing workflows directly. That would over-expand the runtime into builder
product territory. v0.13 keeps Cognition as a bounded Agent CRUD/runtime
backend and lets builders provide IAM, UX, policy, and credentials.

### Keep local/host execution as transparent fallback

Transparent fallback improves development convenience but weakens the promise
that model-directed execution is isolated from the Cognition host. v0.13 keeps
unsafe local modes available only behind explicit development settings.

## Consequences

### Positive

- New projects cannot accidentally run a platform-owned default Agent.
- Same-name Agents in sibling scopes are isolated by construction.
- Long-running sessions pin the Agent/dependency identity they started with.
- Cache reuse cannot silently cross scope, Agent revision, manifest, model, or
  sandbox identity.
- Production deployments fail closed when sandbox isolation is unavailable.
- Callback egress and telemetry export behavior become operator-bounded.

### Negative

- Builders must provision at least one Agent before creating sessions.
- Existing sessions that referenced removed default Agents need compatible
  builder-owned definitions before cutover.
- Mixed v0.12/v0.13 writers are unsupported during migration.
- Some development conveniences now require explicit unsafe settings.
- Release validation needs broader isolation and migration tests than a
  single-tenant runtime.

## Migration and rollback

1. Provision replacement builder-owned Agents in each required scope.
2. Update clients to send `agent_name` on session creation.
3. Drain active runs and pause v0.12 writes.
4. Back up persistence.
5. Apply the v0.13 migration that backfills canonical scope keys and Agent
   revision/digest identity.
6. Replace the complete worker set with v0.13 workers. Mixed v0.12/v0.13
   writers are unsupported.
7. Run cross-scope, cache, callback, OTLP, sandbox, and migration smoke tests.

Rollback before new writes can restore the v0.12 deployment and persistence
backup. After new v0.13 writes, roll forward or restore the backup; do not run
mixed writers against the migrated schema.

## Verification

Implementation and regression coverage live on `release/v0.13.0`:

- `tests/e2e/test_scenarios/p3_agents/test_explicit_agent_provisioning.py`
- `tests/integration/test_v013_postgres_scale.py`
- `tests/unit/test_runtime_manifest.py`
- `tests/unit/test_otlp_bounding.py`
- `tests/unit/test_v013_cache_bounds.py`
- `tests/unit/test_v013_storage_isolation.py`
- `tests/unit/test_v013_strict_execution.py`
- `tests/unit/api/test_agents_crud.py`
- `tests/unit/test_alembic_migrations.py`
- `tests/e2e/test_session_lifecycle.py`
- `tests/e2e/test_workflow.py`

Observed validation before this ADR was added:

- Branch CI passed for `release/v0.13.0` at commit `47d64db`, including Python
  3.11 and 3.12 unit/type/lint gates, A2A TCK on Python 3.12, and Docker cache
  warm jobs.
- Local validation passed for Ruff, strict mypy, strict MkDocs, `git diff
  --check`, full pytest, E2E minus external scenario server, A2A TCK, and the
  CI-sized PostgreSQL 16 fixture.

Release gates still pending by operator choice:

- Official pre-release multi-architecture image push/manifest workflow.
- Live production-profile sandbox/Kubernetes validation.

## Architecture model updates

- `ROADMAP.md`
- `docs/concepts/agent-runtime.md`
- `docs/concepts/architecture.md`
- `docs/concepts/sessions-and-messages.md`
- `docs/guides/api-reference.md`
- `docs/guides/configuration.md`
- `docs/guides/deployment.md`
