# Code-Derived Architecture Risks

**Status:** Current audit register  
**Audit baseline:** `release/v0.13.0` architecture, verified against commit `890e1ad`  
**Last verified:** 2026-07-26

This register records architectural constraints observed directly in code. It is
not a substitute for issues or `ROADMAP.md`; it ensures future architecture work
does not lose the evidence discovered during the code audit.

## How to use this register

- **Open:** Verified behavior with unresolved architectural consequence.
- **Proposed:** A shaped release direction exists but code is unchanged.
- **Accepted:** Deliberate constraint with documented operating guidance.
- **Resolved:** Code and tests close the risk; retain the row and link evidence.

When implementation begins, add the roadmap/issue reference and target release.
Do not mark a row resolved from prose alone.

Rows resolved by v0.13 link to [ADR-0001](decisions/0001-v013-exact-scope-agent-runtime-boundary.md)
and the release-branch tests that prove the boundary. Remaining open rows are
inputs to future roadmap work.

## Multi-tenancy and state integrity

| ID | Status | Observation | Consequence | Primary evidence |
| --- | --- | --- | --- | --- |
| AR-001 | Resolved — v0.13 | Session/message/run/event storage methods commonly accept an ID without effective scope; routes fetch and check afterward | Storage-level methods now carry exact scope for runtime access; wrong-scope identifiers return not-found | `tests/unit/test_v013_storage_isolation.py`; ADR-0001 |
| AR-002 | Open | Session idempotency lookup scans all sessions and does not apply its scope argument | Identical builder idempotency keys can collide across scopes | `api/routes/sessions.py::_find_session_by_idempotency_key` |
| AR-003 | Resolved — v0.13 runtime records | Session deletion removes the session row but has no schema cascade or complete service cleanup | Scoped cleanup now covers runtime-owned records and cache/session-service teardown; deployment storage retention remains an operator concern | `tests/unit/test_v013_storage_isolation.py`; `tests/unit/test_v013_cache_bounds.py` |
| AR-004 | Open | One active run per session is check-then-insert without a database uniqueness invariant | Concurrent requests can create competing foreground attempts | `runtime_projection.py::begin_run`; `storage/schema.py::session_runs_table` |
| AR-005 | Open | Run creation, task link, session update, and event append are separate storage calls | Failure can leave partially projected lifecycle state | `runtime_projection.py::begin_run`, `transition_run_with_event` |
| AR-006 | Open | SQLite allocates event sequence with `MAX+1` and no retry; PostgreSQL uses an advisory lock | Concurrent SQLite event appends can collide | `storage/sqlite.py::append_event`; `storage/postgres.py::append_event` |
| AR-007 | Open | Config subset resolution has no deterministic tie-break for same-depth incomparable scopes | Two valid visible definitions can resolve differently by row order | `storage/config_registry.py` adapter `_get_entity` implementations |
| AR-008 | Resolved — v0.13 | API-created Agents currently use hierarchical ConfigRegistry fallback | API-created Agents resolve only at the exact trusted scope; shared file Agents are explicit read-only fallbacks | `tests/unit/api/test_agents_crud.py`; `tests/e2e/test_scenarios/p3_agents/test_explicit_agent_provisioning.py`; ADR-0001 |
| AR-009 | Open | Checkpoints are keyed by globally unique `thread_id`, not scope; surrounding code supplies isolation | A missed outer scope check reaches the checkpoint namespace directly | `agent/runtime.py`; storage checkpointer methods |

## Configuration and runtime composition

| ID | Status | Observation | Consequence | Primary evidence |
| --- | --- | --- | --- | --- |
| AR-010 | Resolved — v0.13 | Compiled graph cache keys include tool/middleware/subagent counts rather than full resolved contents; skill/MCP/profile contents are absent | Runtime manifest digests, Agent revisions, model identity, sandbox backend identity, and scope fingerprints isolate graph reuse | `tests/unit/test_runtime_manifest.py`; `tests/unit/test_v013_cache_bounds.py`; ADR-0001 |
| AR-011 | Resolved — v0.13 | Production wiring subscribes only `DefaultConfigStore.on_config_change`; graph cache invalidation has no caller | Agent revisions and manifest digests make changed definitions compile under new cache identities; bounded TTL/LRU limits stale residency | `tests/unit/test_runtime_manifest.py`; `tests/unit/test_v013_cache_bounds.py` |
| AR-012 | Open | SQLite/memory registry writes do not emit through the created in-process dispatcher | Claimed immediate local hot reload is not wired | `storage/config_registry.py`; `config_dispatcher.py` |
| AR-013 | Open | PostgreSQL listeners share one global `processed` flag | One replica can consume a change before another sees it; invalidation is best effort | `config_dispatcher.py::PostgresListenDispatcher._process_pending` |
| AR-014 | Resolved — v0.13 | Cache hits return a fresh sandbox handle alongside a graph compiled with the original backend | Cached graphs route dynamically to the current run sandbox and cannot retain another session's backend | `tests/unit/test_v013_cache_bounds.py`; `tests/unit/test_v013_strict_execution.py` |
| AR-015 | Resolved — v0.14 | Empty Agent tool allow-list loads every enabled visible registry tool | Cognition no longer loads ConfigRegistry Python tool records or exposes `AgentDefinition.tools` | `agent/resolver.py`; `llm/deep_agent_service.py`; ROADMAP architectural change 2026-08-04 |
| AR-016 | Resolved — v0.14 | Registry Python tool source is compiled/executed in the server process | Cognition no longer exposes `/tools` or loads API/file Python tool records at runtime | `api/routes`; `agent/resolver.py`; `tests/e2e/test_scenarios/p3_security/test_ast_security_scanning.py` |
| AR-017 | Open | Runtime delegation is built from other visible Agents, including hidden/mode variants; explicit Agent subagent fields are not the sole selection source | Delegation surface can exceed the definition a builder expects | `llm/deep_agent_service.py::_resolve_agent_config` |
| AR-018 | Open | MCP trusted-scope interceptor fills absent keys but does not overwrite conflicts | Model-supplied conflicting scope arguments may survive | `agent/mcp_client.py` interceptor construction |

## Replica, streaming, and lifecycle

| ID | Status | Observation | Consequence | Primary evidence |
| --- | --- | --- | --- | --- |
| AR-019 | Open | Active runtimes, abort handles, graph cache, rate limits, sandbox handles, watchers, and native SSE buffers are process-local while Helm defaults to three replicas | Immediate abort, rate limiting, file config, and in-flight recovery are not deployment-wide | `deep_agent_service.py::SessionAgentManager`; `rate_limiter.py`; Helm values |
| AR-020 | Open | Native SSE creates a new replay buffer per request | `Last-Event-ID` cannot replay the prior request across reconnects or replicas | `api/sse.py::SSEStream`; message route construction |
| AR-021 | Accepted constraint | A2A retention runs opportunistically per active Agent/scope rather than from a global scheduler | Inactive scopes are not cleaned until another matching A2A request | `protocols/a2a/retention.py::A2ARetentionManager` |
| AR-022 | Open | Separately created ConfigRegistry and ArtifactStore pools are not explicitly closed by lifespan | Shutdown can leave connections to process teardown rather than orderly close | `main.py::lifespan`; `storage/factory.py` |

## Execution and security

| ID | Status | Observation | Consequence | Primary evidence |
| --- | --- | --- | --- | --- |
| AR-023 | Open | Docker execution accepts a timeout but does not apply it to `exec_run` | A command can outlive the requested deadline | `execution/backend.py::DockerExecutionBackend.execute` |
| AR-024 | Open | Docker sandbox wrapper has no manager-visible `terminate()` | Session release does not close the corresponding container through the common path | `agent/sandbox_backend.py::CognitionDockerSandboxBackend`; `SessionAgentManager` |
| AR-025 | Accepted constraint | Direct write guards do not constrain arbitrary shell commands | Protected paths are policy for file APIs, not a security boundary for trusted command execution | Sandbox adapter `write`, `edit`, and `execute` methods |
| AR-026 | Open | Kubernetes labels copy raw effective-scope values | Values can violate label constraints or expose scope in cluster metadata | `agent/cognition_agent.py::create_cognition_agent` |
| AR-027 | Resolved — v0.13 origin gate | Completion callback accepts caller-provided HTTP/S destinations without allowlist, private-address defense, or signature | Completion callbacks default denied and require exact operator-approved HTTPS origins before execution starts | `tests/unit/test_v013_strict_execution.py`; `api/routes/messages.py::_approved_callback_origin` |

## Operability and delivery

| ID | Status | Observation | Consequence | Primary evidence |
| --- | --- | --- | --- | --- |
| AR-028 | Open | `/ready` always reports true; `/health` lists every session | Readiness does not prove dependencies and health cost grows with data | `main.py::ready_check`, `health_check` |
| AR-029 | Resolved — v0.13 | HTTP metric endpoint label used concrete URL paths | Metrics now use the matched route template or `unmatched` and status class labels, avoiding resource-ID cardinality | `api/middleware.py::ObservabilityMiddleware`; `tests/unit/test_observability_cardinality.py` |
| AR-030 | Resolved — v0.13 | Metrics startup was coupled to the OpenTelemetry enabled flag | `COGNITION_METRICS_ENABLED` controls Prometheus startup independently from trace export | `main.py::lifespan`; `settings.py`; `tests/unit/test_observability_config.py` |
| AR-031 | Resolved — v0.13 | Cognition startup carried an obsolete MLflow tracing shim while semantic traces already flowed through canonical OTLP and the Collector | Removed the MLflow startup shim; MLflow is now only a Collector destination with the experiment id configured by the operator-owned pipeline | `observability/__init__.py`; `docker/otel-collector-config.yml`; `tests/unit/test_observability_config.py` |
| AR-032 | Resolved — v0.13 | General 500 responses included `str(exc)` | Unhandled 500 responses now return a generic error body and log only redacted error classification with route-template context | `main.py::general_exception_handler`; `tests/unit/test_rest_api.py` |
| AR-033 | Open | FastAPI startup uses `create_all`/manual checks rather than Alembic upgrade | A new image can start against a partially upgraded schema unless operators migrate first | `main.py`; backend `initialize`; `storage/migrations.py` |
| AR-034 | Open | CI omits full E2E, migration-upgrade, Helm lint, and image vulnerability gates | Release automation does not prove every documented deployment path | `.github/workflows/ci.yml`; `pre-release-images.yml` |
| AR-035 | Open | `cognition-client` entry point references a TUI module absent from the inspected client tree | A published console entry point may be unusable | `pyproject.toml`; `client/` |
| AR-036 | Resolved — v0.13 branch | A measured short Agent run produced 209 spans, 172 routine middleware-hook spans, and 742,119 bytes of attributes, including repeated Agent content; a later smoke test also exposed separate Cognition and LangGraph traces for one turn | The final local Compose trace `tr-f0174459e7d636b60018689dfe78b4aa` has one `cognition.agent.run` root, the native LangGraph/model tree beneath it, 20 spans, no orphaned children, no duplicate metrics-adapter spans, and a persisted matching durable trace ID. Raw content remains builder-owned and byte-bounded at export. | [curated tracing proposal](../proposals/curated-opentelemetry-tracing.md); [ADR-0002](decisions/0002-curated-opentelemetry-agent-tracing.md) |
| AR-037 | Resolved — v0.13 branch | Usage Events counted generated text and applied a hard-coded price estimate instead of projecting provider-reported usage metadata | The final local provider run reports a complete Usage Event of 8,996 input and 30 output tokens, the MLflow trace total matches exactly, Prometheus receives the same automatic histogram values, the final message keeps `token_count=null`, and `estimated_cost` remains null. | [curated tracing proposal](../proposals/curated-opentelemetry-tracing.md#separate-workstream-authoritative-usage-events); [ADR-0002](decisions/0002-curated-opentelemetry-agent-tracing.md) |

## Review rules

- Link a roadmap item or issue before changing a row to **Proposed**.
- Add regression/isolation evidence before changing a row to **Resolved**.
- Move deliberate permanent behavior to an ADR and mark it **Accepted**.
- Review this register during every minor-release architecture audit.

## Related views

- [State and configuration](05-state-and-configuration.md)
- [Execution and sandboxes](06-execution-and-sandboxes.md)
- [Deployment and operations](08-deployment-and-operations.md)
- [Governance and evolution](09-governance-and-evolution.md)
