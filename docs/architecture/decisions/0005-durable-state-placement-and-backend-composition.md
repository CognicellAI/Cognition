# ADR-0005: Durable State Placement and Backend Composition

**Status:** Accepted  
**Date:** 2026-08-03  
**Deciders:** Cognition maintainers  
**Supersedes:** Host-backed production durable file storage  
**Related roadmap/issue:** `ROADMAP.md` — S3-compatible storage and Deep Agents backend alignment

## Context

Cognition can run as a horizontally scalable, multi-tenant backend. Durable
state on a Cognition host couples data to a replica, complicates recovery, and
risks cross-scope leakage. Deep Agents provides a backend protocol that can
route transient state, durable virtual files, and sandbox workspaces to
appropriate storage systems.

Database and S3-compatible storage provide a portable distributed topology.
SQLite, in-memory, and local filesystem implementations remain valid choices
for local development and any other deployment where the builder accepts their
durability and isolation properties. Cognition cannot reliably infer deployment
intent from an environment label.

## Decision

1. The configured database backend is authoritative for Agent identity/revisions,
   configuration manifests, sessions, runs, events, messages, checkpoints, and
   other transactional metadata.
2. Skills are builder-mounted files in the selected isolated sandbox workspace;
   they are not persisted in Cognition configuration or object storage. In the
   recommended distributed topology, S3-compatible object
   storage holds independently mutable durable file bodies: artifacts, files,
   memories, contracts, evaluations, and policy bodies. Database manifests carry
   canonical scope, path, object key, checksum, version, and audit metadata;
   knowledge of an object key is not authorization.
3. A composite Deep Agents backend uses the isolated sandbox backend for native
   Skills discovery and execution, and `ArtifactBackend` routes for scoped
   durable virtual files. S3 is an implementation detail below artifact/file
   persistence, not a Deep Agents filesystem backend.
4. Object publication verifies the body checksum and atomically advances the
   database manifest. Sandboxes are ephemeral and never a durable source of
   record.
5. Cognition uses exactly the storage topology selected by the builder. If a
   selected database, object store, or route is unavailable, the operation
   fails explicitly and never falls back to host-local storage or another
   backend.
6. Cognition does not implement a production classifier or reject SQLite,
   in-memory, or local filesystem backends based on deployment labels. Builders
   own storage admission and durability policy. All selected backends preserve
   the same virtual-path and exact-scope semantics where applicable.

## Alternatives considered

### Persist durable files on the Cognition host

This binds state to one replica and undermines portable, horizontally scaled
operation. It is not the recommended distributed topology, but remains a
supported builder-selected backend rather than an environment-gated mode.

### Put all durable file bodies in the relational database

The database remains authoritative for metadata and transactions, but large or
numerous file bodies are better served by S3-compatible object storage. The
manifest/object split provides both transactional publication and scalable body
storage.

### Use S3 as the only authority without manifests

Object listings and keys do not provide the exact-scope authorization,
versioning, query, and atomic activation guarantees required by the runtime.
Database manifests remain authoritative.

## Consequences

### Positive

- Stateless Cognition replicas can serve the same durable scoped state.
- Garage, AWS S3, and other S3-compatible stores share one distributed
  abstraction.
- Deep Agents sees native sandbox Skills and stable artifact/file routes while
  durable body placement changes below Cognition persistence.

### Negative

- The recommended distributed topology requires database and S3-compatible
  operational dependencies.
- Object-store lifecycle, encryption, backup, and recovery are operator
  responsibilities.
- Durable writes require coordination between object upload and manifest
  publication.

## Migration and rollback

1. Provision the database and S3-compatible bucket/prefix policy.
2. Copy existing durable bodies, verify checksums, and create matching scoped
   manifests.
3. Drain active runs before switching workers to the composite
   backend.
4. Preserve a database/object-store backup. Roll back only before new-format
   writes or restore both stores together; do not run mixed backends against
   the same runtime state.

## Verification

- Garage-backed S3 integration tests cover upload, read, list, versioning,
  checksum verification, and exact-scope isolation.
- Backend-selection tests prove that configured database/S3, SQLite, in-memory,
  and local filesystem routes are honored without an environment classifier.
- Failure of a selected backend is explicit and never causes an implicit local
  fallback.
- Sandbox workspaces remain ephemeral and do not become a durable artifact
  source.
- Object-store failures and manifest-publication failures are explicit,
  redacted, and do not report an uncommitted durable write as successful.

## Architecture model updates

- `docs/proposals/v0.14.0-deep-agents-skills-mcp-storage.md`
- `docs/architecture/decisions/index.md`
- `docs/guides/configuration.md`
- `docs/guides/deployment.md`
