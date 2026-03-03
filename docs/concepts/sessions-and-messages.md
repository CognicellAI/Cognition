# Sessions & Messages

A **session** is the unit of conversation in Cognition. It owns a thread of messages, binds to a specific agent, carries optional tenant scope, and persists across server restarts. Every message sent to a session streams back over Server-Sent Events (SSE).

---

## Session Lifecycle

```
POST /sessions                →  Session created (status: active)
    │
POST /sessions/{id}/messages  →  User message persisted, agent streams response
    │                              (token events, tool calls, tool results, ...)
    │                              done event → assistant message persisted
    │
POST /sessions/{id}/abort     →  In-progress stream cancelled gracefully
    │
DELETE /sessions/{id}         →  Session and all messages deleted
```

Sessions start with `status: active`. The `active`/`inactive`/`error` states are tracked in `server/app/models.py:SessionStatus`.

---

## Session Model

Defined in `server/app/models.py:Session`:

| Field | Type | Description |
|---|---|---|
| `id` | `str` (UUID) | Unique session identifier |
| `thread_id` | `str` (UUID) | LangGraph checkpoint thread ID; one-to-one with session |
| `title` | `str` | Human-readable session name |
| `status` | `SessionStatus` | `active`, `inactive`, or `error` |
| `agent_name` | `str` | Name of the bound agent (default: `"default"`) |
| `config` | `SessionConfig` | Per-session LLM overrides (provider, model, temperature) |
| `scopes` | `dict[str, str]` | Tenant isolation values (e.g. `{"user": "alice"}`) |
| `created_at` | `datetime` | Creation timestamp |
| `updated_at` | `datetime` | Last-modified timestamp |
| `message_count` | `int` | Running count of messages in the session |
| `workspace_path` | `str` | Absolute path to the agent's workspace |

### Creating a Session

```bash
# Default agent
curl -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"title": "My session"}'

# Specific agent
curl -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"title": "Code review", "agent_name": "readonly"}'
```

### Per-Session LLM Override

A session can override the server's default LLM:

```bash
curl -X PATCH http://localhost:8000/sessions/{id} \
  -H "Content-Type: application/json" \
  -d '{"config": {"model": "gpt-4o-mini", "temperature": 0.2}}'
```

If only `model` is provided, Cognition infers the provider via the `DiscoveryEngine`.

---

## Message Model

Defined in `server/app/models.py:Message`:

| Field | Type | Description |
|---|---|---|
| `id` | `str` (UUID) | Unique message identifier |
| `session_id` | `str` | Parent session |
| `role` | `str` | `user`, `assistant`, `system`, or `tool` |
| `content` | `str` | Message text |
| `tool_calls` | `list[ToolCall]` | Tool invocations made by this assistant turn |
| `tool_call_id` | `str \| None` | For `tool` role messages, the call this responds to |
| `token_count` | `int \| None` | Token count for the message |
| `model_used` | `str \| None` | Model that produced this message |
| `parent_id` | `str \| None` | Parent message for threaded structure |
| `created_at` | `datetime` | Creation timestamp |

### Persistence Model

Messages are persisted in two stages:

1. **User message** — Written to the `StorageBackend` immediately when `POST /sessions/{id}/messages` is called, before the agent starts.
2. **Assistant message** — Accumulated as `token` events stream in. Written to the `StorageBackend` atomically when the `done` event fires.

This means a session always has a complete, gap-free history of completed turns in persistent storage. In-progress turns are not written until they complete (or fail with an `error` event, which also triggers persistence).

---

## SSE Streaming

Cognition uses [Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html) for streaming. Every call to `POST /sessions/{id}/messages` returns a `text/event-stream` response.

### Wire Format

Each event follows the SSE protocol:

```
id: 42
event: token
data: {"content": "Here"}

id: 43
event: token
data: {"content": " is"}

id: 44
event: done
data: {"assistant_data": {...}}

```

### Event Types

All event types are defined in `server/app/agent/runtime.py` and serialized to SSE via `server/app/api/sse.py:EventBuilder`:

| Event | `event:` field | Key `data` fields | Description |
|---|---|---|---|
| Token | `token` | `content: str` | A single LLM output token |
| Tool call | `tool_call` | `name: str`, `args: dict`, `id: str` | Agent invoking a tool |
| Tool result | `tool_result` | `tool_call_id: str`, `output: str`, `exit_code: int` | Tool execution result |
| Planning | `planning` | `todos: list[str]` | Agent creating a task plan |
| Step complete | `step_complete` | `step_number: int`, `total_steps: int`, `description: str` | A plan step finished |
| Delegation | `delegation` | `target_agent: str`, `task: str` | Primary agent delegating to a subagent |
| Status | `status` | `status: "thinking" \| "idle"` | Agent status change |
| Usage | `usage` | `input_tokens: int`, `output_tokens: int`, `estimated_cost: float`, `provider: str`, `model: str` | Token accounting |
| Error | `error` | `message: str`, `code: str` | Recoverable error |
| Done | `done` | `assistant_data: dict` | Stream complete; contains the full assistant message |

### Reading an SSE Stream

```bash
# -N disables curl buffering so tokens appear as they arrive
curl -N -X POST http://localhost:8000/sessions/${SESSION}/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "List the files in this directory."}'
```

Python example using `httpx`:

```python
import httpx, json

with httpx.stream(
    "POST",
    f"http://localhost:8000/sessions/{session_id}/messages",
    json={"content": "List files."},
) as r:
    for line in r.iter_lines():
        if line.startswith("data:"):
            event = json.loads(line[5:])
            print(event)
```

---

## Reconnection

The SSE stream implements automatic reconnection via the `Last-Event-ID` mechanism, implemented in `server/app/api/sse.py`.

### How It Works

1. Every event is assigned a sequential numeric ID and sent as `id: <n>`.
2. The server maintains an `EventBuffer` (default capacity: 100 events) per session in memory.
3. The client sends the `Last-Event-ID` header on reconnection.
4. The server replays any buffered events with IDs greater than `Last-Event-ID`.
5. A `reconnected` event is sent first to confirm the reconnection, followed by replayed events.

### Heartbeat

The server sends a keepalive comment every 15 seconds (configurable via `COGNITION_SSE_HEARTBEAT_INTERVAL`):

```
:heartbeat

```

This prevents proxies and load balancers from closing idle SSE connections. The comment is invisible to application-level event handlers.

### Reconnection Configuration

| Setting | Environment Variable | Default |
|---|---|---|
| SSE retry hint | `COGNITION_SSE_RETRY_INTERVAL` | `3000` ms |
| Heartbeat interval | `COGNITION_SSE_HEARTBEAT_INTERVAL` | `15.0` s |
| Event buffer size | `COGNITION_SSE_BUFFER_SIZE` | `100` events |

---

## Message Pagination

`GET /sessions/{id}/messages` returns messages in reverse-chronological order with offset-based pagination:

```bash
# First page (most recent 50)
curl "http://localhost:8000/sessions/${SESSION}/messages"

# Second page
curl "http://localhost:8000/sessions/${SESSION}/messages?limit=50&offset=50"
```

Response:

```json
{
  "messages": [...],
  "total": 142,
  "has_more": true
}
```

---

## Abort

`POST /sessions/{id}/abort` cancels any in-progress agent operation. The streaming response stops emitting events, the assistant message is persisted with whatever content was accumulated, and the session returns to idle.

```bash
curl -X POST http://localhost:8000/sessions/${SESSION}/abort
```

Response:

```json
{"success": true, "message": "Operation aborted"}
```

Abort is implemented via a thread-ID-based cancellation set in `DeepAgentRuntime`. On the next event-processing iteration, the runtime detects the abort flag and exits the stream loop cleanly.
