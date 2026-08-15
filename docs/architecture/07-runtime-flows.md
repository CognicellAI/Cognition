# Dynamic View: Runtime Flows

**Status:** Current code-derived model  
**Code baseline:** `release/v0.13.0` (`890e1ad`)  
**Last verified:** 2026-07-25

Static C4 diagrams show ownership; these sequences show when state becomes
durable and where process-local behavior remains.

## Single-Agent full-stack lifecycle

![Single Agent full-stack lifecycle from definition through scoped execution, streaming, persistence, and teardown](assets/single-agent-lifecycle.svg)

The lifecycle view follows one Agent across the builder boundary, API adapters,
runtime composition, execution dependencies, durable state, and observability.
The detailed sequences below expand the startup, native, A2A, approval, and
configuration-change paths.

## Startup

```mermaid
sequenceDiagram
    autonumber
    participant Operator
    participant FastAPI as FastAPI lifespan
    participant Storage as StorageBackend
    participant Registry as ConfigRegistry/ConfigStore
    participant Artifact as ArtifactStore
    participant Runtime as Runtime services
    participant Optional as A2A/watcher/telemetry/rate limit

    Operator->>FastAPI: Start Uvicorn
    FastAPI->>Storage: create + initialize
    Note over Storage: create_all and backend compatibility checks<br/>do not run Alembic upgrade
    FastAPI->>Registry: create registry and schema
    FastAPI->>Registry: seed provider/profile/skill/tool/Agent sources
    FastAPI->>Artifact: create + initialize
    FastAPI->>Runtime: create resolver, session manager, Agent manager
    FastAPI->>Optional: start config dispatcher
    opt A2A enabled
        FastAPI->>Optional: mount Agent Card and JSON-RPC routes
    end
    FastAPI->>Optional: create model catalog and validate K8s
    FastAPI->>Optional: start file watcher, tracing, metrics, MLflow, limiter
    FastAPI-->>Operator: ready to accept requests
```

Database migrations are an operator-controlled prerequisite. `/ready` reports
true after startup completes; it does not perform an active dependency probe.

## Create a session and run a native SSE turn

```mermaid
sequenceDiagram
    autonumber
    actor Client
    participant Gateway as Trusted ingress
    participant Sessions as Session API
    participant Messages as Message/SSE API
    participant Task as AgentTaskRuntime
    participant Store as StorageBackend
    participant Manager as SessionAgentManager
    participant Service as DeepAgentStreamingService
    participant Resolver as RuntimeResolver/ConfigStore
    participant Factory as Agent graph factory
    participant Graph as DeepAgentRuntime
    participant Model as Model provider
    participant Sandbox as Sandbox/MCP tools
    participant Projector as RuntimeProjectionService
    participant Callback as Optional callback

    Client->>Gateway: Create session
    Gateway->>Gateway: authenticate + authorize scope
    Gateway->>Sessions: POST /sessions + explicit agent_name + scope headers
    Sessions->>Resolver: validate primary Agent in scope
    Sessions->>Store: create session + thread + immutable scope
    Sessions->>Manager: register session service
    Sessions-->>Client: session

    Client->>Gateway: Send message
    Gateway->>Messages: POST /sessions/{id}/messages + scope
    Messages->>Messages: rate limit and validate session/scope/status
    Messages->>Task: submit task
    Task->>Store: idempotency lookup and create task/run/user message
    Task->>Projector: begin run
    Projector->>Store: update task/session and append run event

    Messages->>Manager: obtain/register service
    Manager->>Service: stream_response
    Service->>Resolver: resolve scoped Agent, tools, MCP, model
    Service->>Store: obtain checkpointer and Store
    Service->>Factory: assemble or reuse graph + create sandbox handle
    Factory-->>Service: DeepAgentRuntime
    Service->>Graph: astream(thread_id, CognitionContext)

    loop model and tool loop
        Graph->>Model: model invocation
        Model-->>Graph: message/update chunks
        opt tool call
            Graph->>Sandbox: tool or filesystem/command operation
            Sandbox-->>Graph: result
        end
        Graph-->>Service: typed AgentEvent
        Service-->>Messages: AgentEvent
        Messages->>Projector: append durable correlated event
        Projector->>Store: persist event/projection
        Messages-->>Client: SSE event
    end

    Messages->>Task: persist assistant message/artifact
    Messages->>Projector: terminal transition
    Projector->>Store: persist final run/task/session/event state
    Messages-->>Client: done or error SSE
    opt callback URL supplied
        Messages->>Callback: POST completion payload
    end
```

The route emits token deltas and rich runtime events while durable events carry
session, task, run, sequence, trace, and span correlation. The message table is
a read projection; the LangGraph checkpoint remains authoritative conversation
state.

Native `SSEStream` uses a request-local circular buffer. A new request cannot
read the previous request's buffer, so `Last-Event-ID` is not durable replay.
Clients that need recovery should query persisted runs/events. A2A subscription
uses durable task events and has stronger replay semantics.

## A2A streaming task

```mermaid
sequenceDiagram
    autonumber
    actor A2AClient as A2A client
    participant Gateway as Trusted ingress
    participant Routes as A2A routes/dispatcher
    participant Executor as CognitionA2AExecutor
    participant Task as AgentTaskRuntime
    participant Artifact as ArtifactStore
    participant Service as Agent streaming service
    participant Store as StorageBackend

    A2AClient->>Gateway: GET Agent Card
    Gateway->>Routes: scope headers + selected Agent
    Routes->>Routes: resolve visible, exposed Agent
    Routes-->>A2AClient: private Agent Card + ETag

    A2AClient->>Gateway: SendStreamingMessage JSON-RPC
    Gateway->>Routes: authenticated request + authorized scope
    Routes->>Routes: validate A2A version/content and build trusted call context
    Routes->>Executor: normalized A2A message
    Executor->>Executor: canonical request fingerprint
    Executor->>Task: submit or recover idempotent task
    Task->>Store: exact Agent/scope task lookup and run creation
    Executor->>Artifact: persist inert input Parts
    Executor->>Service: execute run

    loop runtime output
        Service-->>Executor: typed runtime event
        Executor->>Store: persist status/message/event
        opt output artifact chunk
            Executor->>Artifact: persist/coalesce artifact before emit
        end
        Executor-->>A2AClient: A2A task/status/message/artifact event
    end

    Executor->>Store: terminal task/run state
    Executor-->>A2AClient: terminal task event

    opt reconnect or later subscription
        A2AClient->>Routes: SubscribeToTask/GetTask
        Routes->>Store: exact scoped durable lookup/events
        Store-->>A2AClient: replay/current state
    end
```

Text, data, raw, and URL Parts are normalized without fetching URL content.
Message IDs are namespaced by Agent and complete effective scope. Cancellation,
polling, listing, and subscription all use durable `RuntimeTask` state.

## Human approval and resume

```mermaid
sequenceDiagram
    autonumber
    actor Client
    participant Runtime as DeepAgentRuntime
    participant Graph as LangGraph checkpoint
    participant API as Session API/SSE
    participant Projector as RuntimeProjectionService
    participant Store as StorageBackend

    Runtime->>Graph: tool reaches approval middleware
    Graph-->>Runtime: interrupt with action request
    Runtime-->>API: InterruptEvent + waiting status
    API->>Projector: persist waiting/input-required transition
    Projector->>Store: run/task/session/event updates
    API-->>Client: interrupt SSE

    Client->>API: POST /sessions/{id}/resume
    API->>Store: load scoped session/task/checkpoint
    API->>Projector: begin continuation attempt when task-backed
    API->>Runtime: Command(resume=approve/edit/reject)
    Runtime->>Graph: continue same thread checkpoint
    Graph-->>Runtime: resumed stream
    Runtime-->>API: decision, content, tool, or terminal events
    API->>Projector: persist continued lifecycle
    API-->>Client: JSON or SSE response
```

Task-backed continuations create another run attempt beneath the same durable
task. A compatibility path exists for older runs that predate runtime-task
correlation.

## Cancellation

`AgentTaskRuntime.cancel` persists task cancellation before requesting
process-local interruption. If an active run exists, the serving process asks
`SessionAgentManager` to abort the thread and projects the run to an aborted
terminal state. Persist-first ordering makes polling observe cancellation even
if runtime interruption races or the process exits.

Because abort handles are process-local, a request reaching another replica can
persist the durable state without necessarily owning the live graph operation.
Deployment design must distinguish durable cancellation intent from immediate
same-process interruption.

## Configuration change

```mermaid
sequenceDiagram
    autonumber
    actor Builder
    participant API as Config CRUD route
    participant Registry as ConfigRegistry
    participant DB as config_entities/config_changes
    participant Notify as LISTEN/NOTIFY dispatcher
    participant Facade as DefaultConfigStore
    participant Cache as Compiled graph cache

    Builder->>API: write definition
    API->>Registry: validate and upsert
    Registry->>DB: update entity + append change
    opt PostgreSQL
        Registry->>Notify: NOTIFY channel
        Notify->>DB: read unprocessed changes
        Notify->>Facade: on_config_change
        Notify->>DB: mark globally processed
    end
    Facade->>Facade: evict eligible in-memory Agent entry
    Note over Cache: No production subscriber currently invalidates<br/>the compiled graph cache
    API-->>Builder: response
```

SQLite and memory registries do not currently call the created in-process
dispatcher. PostgreSQL's shared `processed` flag also makes multi-worker
delivery best effort rather than a guaranteed broadcast. These are tracked
architecture gaps, not guarantees.

## Code evidence

| Flow | Primary source |
| --- | --- |
| Startup/shutdown | `server/app/main.py` — `lifespan` |
| Session creation and resume | `server/app/api/routes/sessions.py` |
| Native execution/SSE/callback | `server/app/api/routes/messages.py`; `server/app/api/sse.py` |
| Task lifecycle/cancellation/subscription | `server/app/agent/task_runtime.py` |
| Runtime projection | `server/app/runtime_projection.py` |
| Runtime resolution and streaming | `server/app/llm/deep_agent_service.py`; `server/app/agent/resolver.py` |
| Graph stream translation | `server/app/agent/runtime.py` |
| A2A adaptation | `server/app/protocols/a2a/` |
| Config notification | `server/app/storage/config_registry.py`; `config_dispatcher.py` |

## Related views

- [Server composition](03-server-components.md)
- [Agent runtime components](04-agent-runtime-components.md)
- [State and configuration](05-state-and-configuration.md)
