# C4 Level 3: State and Configuration

**Status:** Current code-derived model  
**Code baseline:** `release/v0.13.0` (`890e1ad`)  
**Last verified:** 2026-07-25

Cognition separates authoritative LangGraph state, durable runtime lifecycle,
read projections, versioned artifacts, and hot-reloadable configuration. These
logical stores can share one SQLite or PostgreSQL database but have different
contracts and consistency rules.

## Persistence component diagram

```mermaid
C4Component
    title Persistence and configuration components

    Container_Boundary(server, "Cognition server") {
        Component(task_service, "AgentTaskRuntime", "Application service", "Protocol-neutral task and attempt lifecycle")
        Component(runtime_projection, "RuntimeProjectionService", "Projector", "Run/task/session summaries and append-only events")
        Component(storage_port, "StorageBackend", "Protocol", "Sessions, messages, tasks, runs, events, checkpointer, and Store")
        Component(message_projection, "Message projection", "Projection adapter", "REST-oriented messages rebuilt from checkpoints")
        Component(config_store, "DefaultConfigStore", "Configuration facade", "Combines native, file, and registry definitions")
        Component(config_registry, "ConfigRegistry", "Scoped configuration store", "Providers, tools, Agents, profiles, and defaults")
        Component(dispatcher, "ConfigChangeDispatcher", "Invalidation bus", "In-process or PostgreSQL LISTEN/NOTIFY delivery")
        Component(artifact_port, "ArtifactStore", "Versioned content store", "Exact-scope artifact CRUD")
        Component(schema, "Schema and migration tooling", "SQLAlchemy + Alembic", "Declares Cognition tables and explicit upgrades")
    }

    ContainerDb(runtime_db, "Cognition tables", "SQLite/PostgreSQL", "Sessions, messages, tasks, runs, events")
    ContainerDb(graph_db, "LangGraph tables", "SQLite/PostgreSQL", "Checkpoints, writes, and cross-thread Store")
    ContainerDb(config_db, "Configuration tables", "SQLite/PostgreSQL", "config_entities and config_changes")
    ContainerDb(artifact_db, "Artifact table", "SQLite/PostgreSQL", "Versioned scoped content")
    Container(workspace, "Workspace definitions", "YAML/Markdown/Python", "Bootstrap Agents, tools, middleware, and defaults")

    Rel(task_service, storage_port, "Creates and queries lifecycle state")
    Rel(task_service, runtime_projection, "Requests transitions")
    Rel(runtime_projection, storage_port, "Updates summaries and appends events")
    Rel(storage_port, runtime_db, "Reads/writes")
    Rel(storage_port, graph_db, "Provides checkpointer and Store")
    Rel(message_projection, graph_db, "Reads authoritative messages")
    Rel(message_projection, runtime_db, "Rebuilds message rows")
    Rel(config_store, config_registry, "Delegates dynamic state")
    Rel(config_store, workspace, "Loads native/file definitions")
    Rel(config_registry, config_db, "Reads/writes entities and changes")
    Rel(config_registry, dispatcher, "Notifies on supported write paths")
    Rel(dispatcher, config_store, "Invalidates cached facade entries")
    Rel(artifact_port, artifact_db, "Reads/writes versions")
    Rel(schema, runtime_db, "Declares/upgrades")
    Rel(schema, config_db, "Declares/upgrades")
    Rel(schema, artifact_db, "Declares/upgrades")
```

## State authority

| Concern | Authoritative record | Derived or supporting record |
| --- | --- | --- |
| Agent conversation and graph state | LangGraph checkpoint keyed by `thread_id` | `messages` REST projection |
| Requested work | `runtime_tasks` | Current/last run links and task metadata |
| One execution attempt | `session_runs` | Session summary fields |
| Ordered runtime evidence | Append-only `session_events` | SSE/A2A transport projections |
| Conversation container | `sessions` | Message count, active/latest run, latest event metadata |
| Dynamic definitions | `config_entities` plus native/file sources | In-process `DefaultConfigStore` entries |
| Agent-produced content | Versioned `artifacts` | Virtual filesystem routes and task artifact descriptors |
| Cross-thread memory | LangGraph Store | Scope is available through `CognitionContext`; these components do not enforce a scoped Store wrapper |

The `messages` table is explicitly rebuildable from checkpoint messages. It is
optimized for pagination, timestamps, threading, tool metadata, token counts,
and model attribution; it is not the graph's source of truth.

## Logical data model

```mermaid
erDiagram
    SESSION ||--o{ MESSAGE : projects
    SESSION ||--o{ RUNTIME_TASK : provides_context
    SESSION ||--o{ SESSION_RUN : attempts
    RUNTIME_TASK ||--o{ SESSION_RUN : correlates
    SESSION_RUN ||--o{ SESSION_EVENT : emits
    SESSION_RUN ||--o{ ARTIFACT : tags
    SESSION ||--|| LANGGRAPH_THREAD : uses
    CONFIG_ENTITY ||--o{ CONFIG_CHANGE : records

    SESSION {
        string id
        string thread_id
        string agent_name
        json scopes
        string status
    }
    RUNTIME_TASK {
        string id
        string session_id
        string agent_name
        json effective_scope
        string scope_key
        string status
    }
    SESSION_RUN {
        string id
        string session_id
        string task_id
        string status
        json effective_scope
    }
    SESSION_EVENT {
        string id
        string session_id
        string run_id
        string task_id
        int sequence
        string event_type
    }
    CONFIG_ENTITY {
        int id
        string entity_type
        string name
        json scope
        json definition
        string source
    }
    ARTIFACT {
        string id
        int version
        string artifact_type
        json scope
        string run_id
    }
```

These are application-level relationships. The Cognition SQLAlchemy schema does
not declare foreign keys between these tables. Lifecycle services must therefore
maintain referential consistency and cleanup explicitly.

## Storage adapters

### Memory

All records, checkpoints, and Store values live in process memory. This is a
test and ephemeral-development adapter, not a restart or replica boundary.

### SQLite

Operational tables use `aiosqlite`; LangGraph supplies its SQLite checkpointer
and Store. The database file is local to the configured workspace. SQLite is
appropriate for a single process and local durability.

### PostgreSQL

Operational state uses an `asyncpg` pool. LangGraph checkpointer and Store,
ConfigRegistry, ArtifactStore, and config notification use separate PostgreSQL
connections or pools. PostgreSQL is the supported shared durable substrate for
multiple server replicas.

## Dynamic configuration

`ConfigRegistry` stores providers, tools, Agents, sandbox profiles, and
global defaults as `(entity_type, name, scope, definition, source)` rows. MCP
servers exist only inside immutable Agent definitions.

Configuration resolution remains hierarchical for configuration entities:

1. A stored scope matches when it is a subset of the request scope.
2. The row with the greatest number of scope keys wins.
3. Empty scope is the global fallback.

`DefaultConfigStore` exposes one facade to routes and `RuntimeResolver`.
Cognition creates no native Agents. Startup bootstrap treats YAML/filesystem
entries as builder definitions and preserves API-managed records where
implemented. API-created Agent CRUD is stricter than general configuration:
Agent rows resolve only at the complete trusted scope, while explicit shared
file Agents may remain read-only fallback definitions.

PostgreSQL writes add a `config_changes` row and issue `NOTIFY`. The listener
queries unprocessed rows and invokes subscribers. An in-process dispatcher is
constructed for SQLite and memory, but current registry write paths do not emit
through it. At startup the only registered subscriber is
`DefaultConfigStore.on_config_change`; compiled graph cache invalidation is not
separately subscribed in the composition root.

## Scope semantics in the current code

Scope behavior is intentionally split between configuration inheritance and
runtime isolation:

- Runtime tasks use a canonical hash plus exact dictionary equality and namespace
  idempotency by Agent and scope.
- Artifact reads and versions use exact scope.
- Config entities intentionally use subset inheritance.
- API-created Agents use exact scope, revision, and definition digests.
- Sessions, runs, messages, events, tasks, artifacts, cleanup, deletion, and
  checkpoint access enforce the exact trusted scope at the storage/runtime
  boundary. Wrong-scope identifiers return not-found.
- Runs persist a redacted manifest and manifest digest so later Agent/config
  updates affect future runs only.

## Schema evolution

Alembic migrations exist and are exposed through the server administration CLI.
The FastAPI startup path does not run `alembic upgrade`; backend
`initialize()` calls `metadata.create_all()` and performs selected compatibility
checks. Operators must treat schema upgrade as an explicit deployment step.

## Known consistency boundaries

- Creating a run, linking its task, updating its session, and appending its first
  event are separate backend calls rather than one database transaction.
- “One active run per session” is enforced through service checks, not a partial
  unique database constraint.
- Event sequence uniqueness is constrained by `(session_id, sequence)`.
  PostgreSQL serializes allocation with an advisory lock; SQLite does not have an
  equivalent retry path.
- Session cleanup is scoped and deterministic for runtime-owned records, but
  operators should still treat durable stores and workspace/artifact storage as
  deployment resources that need backup/retention policy.
- Same-depth matching config scopes have no explicit precedence tie-breaker.

These boundaries belong in architectural review whenever lifecycle,
multi-tenancy, deletion, retry, or replica behavior changes.

## Code evidence

| Responsibility | Primary source |
| --- | --- |
| Storage protocols | `server/app/storage/backend.py` |
| Memory/SQLite/PostgreSQL adapters | `server/app/storage/memory.py`; `sqlite.py`; `postgres.py` |
| Table declarations | `server/app/storage/schema.py` |
| Config facade and precedence | `server/app/storage/config_store.py` |
| Config inheritance and adapters | `server/app/storage/config_registry.py` |
| Config notification | `server/app/storage/config_dispatcher.py` |
| Artifacts | `server/app/storage/artifact_store.py`; `config_models.py` |
| Message rebuild | `server/app/storage/message_projection.py`; `deep_agent_service.py` |
| Runtime lifecycle | `server/app/agent/task_runtime.py`; `server/app/runtime_projection.py` |
| Migrations | `server/alembic/versions/`; `server/app/storage/migrations.py`; `server/app/cli.py` |

## Related views

- [Agent runtime components](04-agent-runtime-components.md)
- [Runtime flows](07-runtime-flows.md)
- [Governance and evolution](09-governance-and-evolution.md)
