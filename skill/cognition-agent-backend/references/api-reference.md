# API Reference

Cognition exposes a REST API with Server-Sent Events (SSE) for streaming responses.

Base URL: `http://localhost:8000`

## Health & Readiness

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check (version, active sessions, timestamp) |
| `GET` | `/ready` | Readiness probe (boolean) |

## Sessions

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions` | Create a new session |
| `GET` | `/sessions` | List sessions (scope-filtered) |
| `GET` | `/sessions/{id}` | Get session details |
| `PATCH` | `/sessions/{id}` | Update session title/config |
| `DELETE` | `/sessions/{id}` | Delete session and messages |
| `POST` | `/sessions/{id}/abort` | Abort current agent operation |

### Example: Create Session

```bash
curl -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"title": "Code Review"}'
```

## Messages & Streaming

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions/{id}/messages` | Send message (returns SSE stream) |
| `GET` | `/sessions/{id}/messages` | List messages (paginated) |
| `GET` | `/sessions/{id}/messages/{msg_id}` | Get specific message |

### Example: Send Message

```bash
curl -N -X POST http://localhost:8000/sessions/{id}/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "Analyze main.py"}'
```

### SSE Event Types

The `/sessions/{id}/messages` endpoint returns a stream of events:

| Event | Fields | Description |
|-------|--------|-------------|
| `token` | `content` | Streaming LLM token |
| `tool_call` | `name`, `args`, `id` | Agent invoking a tool |
| `tool_result` | `tool_call_id`, `output`, `exit_code` | Tool execution result |
| `planning` | `todos` | Agent creating a task plan |
| `step_complete` | `step_number`, `total_steps`, `description` | Plan step completed |
| `status` | `status` | Agent status (`thinking`, `idle`) |
| `usage` | `input_tokens`, `output_tokens`, `estimated_cost`, `provider`, `model` | Token usage |
| `error` | `message`, `code` | Error occurred |
| `done` | `assistant_data` | Stream complete, contains final message |

## Multi-Tenant Scoping

When `COGNITION_SCOPING_ENABLED=true`, all session endpoints require scope headers.
Sessions are isolated per scope combination.

**Required Headers:**
```
X-Cognition-Scope-User: user-123
X-Cognition-Scope-Project: project-456
```

Missing headers will result in `403 Forbidden`.
