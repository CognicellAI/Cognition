# API Reference

Base URL: `http://localhost:8000`

All request and response bodies are JSON. Streaming endpoints return `text/event-stream`.

---

## Health & Readiness

### `GET /health`

Returns server health status.

**Response `200 OK`:**
```json
{
  "status": "healthy",
  "version": "0.4.0",
  "active_sessions": 3,
  "circuit_breakers": [],
  "timestamp": "2026-03-19T12:00:00Z"
}
```

### `GET /ready`

Readiness probe. Returns `200` when the server has completed startup.

**Response `200 OK`:**
```json
{"ready": true}
```

---

## Sessions

### `POST /sessions`

Create a new session.

**Request body:**
```json
{
  "title": "My session",
  "agent_name": "default"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `title` | string (max 200) | No | Human-readable label |
| `agent_name` | string | No | Agent to bind; default `"default"` |

**Headers (when scoping enabled):**
```
X-Cognition-Scope-User: alice
X-Cognition-Scope-Project: proj-123
```

**Response `201 Created`:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "My session",
  "thread_id": "7f3e4a12-...",
  "status": "active",
  "agent_name": "default",
  "created_at": "2026-03-02T12:00:00Z",
  "updated_at": "2026-03-02T12:00:00Z",
  "message_count": 0
}
```

### `GET /sessions`

List sessions for the current workspace. Filtered by scope when scoping is enabled.

**Query parameters:**
- None (all sessions for the workspace/scope are returned)

**Response `200 OK`:**
```json
{
  "sessions": [...],
  "total": 12
}
```

### `GET /sessions/{session_id}`

Get session details.

**Response `200 OK`:** Session object (same schema as create response)  
**Response `404 Not Found`:** Session does not exist or is out of scope.

### `PATCH /sessions/{session_id}`

Update session metadata or LLM configuration.

**Request body (all fields optional):**
```json
{
  "title": "Updated title",
  "agent_name": "readonly",
  "config": {
    "provider_id": "my-openai-config",
    "model": "gpt-4o-mini",
    "temperature": 0.3,
    "provider": "openai",
    "max_tokens": 2048
  }
}
```

**`SessionConfig` fields:**

| Field | Type | Description |
|---|---|---|
| `provider_id` | string | Reference a specific `ProviderConfig` by ID from ConfigRegistry. Takes priority over `provider`/`model`. |
| `provider` | string | Provider type override: `openai`, `anthropic`, `bedrock`, `openai_compatible`, `google_genai`, `google_vertexai` |
| `model` | string | Model ID override |
| `temperature` | float | Temperature (0.0-2.0) |
| `max_tokens` | int | Max output tokens |
| `recursion_limit` | int | Max agent recursion depth |

Provider resolution priority: `provider_id` (exact config lookup) > `provider` + `model` (direct override) > first enabled ProviderConfig from ConfigRegistry.

**Response `200 OK`:** Updated session object  
**Response `404 Not Found`**

### `DELETE /sessions/{session_id}`

Delete a session and all its messages.

**Response `204 No Content`**  
**Response `404 Not Found`**

### `POST /sessions/{session_id}/abort`

Cancel any in-progress agent operation for this session.

**Response `200 OK`:**
```json
{"success": true, "message": "Operation aborted"}
```

**Response `404 Not Found`**

---

## Messages

### `POST /sessions/{session_id}/messages`

Send a user message and receive the agent's streaming response via Server-Sent Events.

**Request body:**
```json
{
  "content": "List the files in the workspace.",
  "model": "gpt-4o-mini"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `content` | string (min length 1) | Yes | The user's message |
| `model` | string | No | Override model for this message only |
| `parent_id` | string | No | Parent message ID for threaded context |

**Headers:**
```
Content-Type: application/json
Last-Event-ID: 42   # Optional; triggers reconnection replay from this event ID
```

**Response `200 OK`:**  
Content-Type: `text/event-stream`

The response is a stream of SSE events. See [SSE Event Types](#sse-event-types) below.

**Errors:**
- `429 Too Many Requests` — Rate limit exceeded
- `404 Not Found` — Session not found
- `403 Forbidden` — Scope mismatch

### `GET /sessions/{session_id}/messages`

List messages in a session with pagination.

**Query parameters:**

| Parameter | Default | Description |
|---|---|---|
| `limit` | `50` | Number of messages to return |
| `offset` | `0` | Number of messages to skip |

**Response `200 OK`:**
```json
{
  "messages": [
    {
      "id": "msg-uuid",
      "session_id": "session-uuid",
      "role": "user",
      "content": "List files.",
      "created_at": "2026-03-02T12:00:00Z",
      "tool_calls": [],
      "token_count": null,
      "model_used": null
    },
    {
      "id": "msg-uuid-2",
      "role": "assistant",
      "content": "Here are the files...",
      "tool_calls": [
        {"name": "bash", "args": {"command": "ls -la"}, "id": "call_xyz"}
      ],
      "token_count": 142,
      "model_used": "gpt-4o"
    }
  ],
  "total": 2,
  "has_more": false
}
```

`role` values: `user`, `assistant`, `system`, `tool`.

### `GET /sessions/{session_id}/messages/{message_id}`

Get a specific message by ID.

**Response `200 OK`:** Message object  
**Response `404 Not Found`**

---

## SSE Event Types

Every event in the `POST /sessions/{id}/messages` stream has the structure:

```
id: <sequential-integer>
event: <event-type>
data: <json-object>

```

### `token`

A single LLM output token. Stream these into a buffer to accumulate the full response.

```json
{"content": "Here"}
```

### `tool_call`

The agent is invoking a tool.

```json
{
  "name": "bash",
  "args": {"command": "ls -la"},
  "id": "call_abc123"
}
```

### `tool_result`

Result of a tool invocation.

```json
{
  "tool_call_id": "call_abc123",
  "output": "total 24\ndrwxr-xr-x ...",
  "exit_code": 0
}
```

`exit_code` is `0` for success, non-zero for failure.

### `planning`

The agent has created a task plan (list of todo items).

```json
{
  "todos": [
    "Read the main configuration file",
    "Identify all API endpoints",
    "Generate the summary"
  ]
}
```

### `step_complete`

A step in the agent's task plan has been completed.

```json
{
  "step_number": 1,
  "total_steps": 3,
  "description": "Read the main configuration file"
}
```

### `delegation`

The primary agent is delegating a subtask to a subagent.

```json
{
  "target_agent": "security-auditor",
  "task": "Audit auth.py for SQL injection vulnerabilities"
}
```

### `status`

The agent's status has changed.

```json
{"status": "thinking"}
```

Values: `thinking` (LLM processing), `idle` (between steps or done).

### `usage`

Token usage and estimated cost for this response.

```json
{
  "input_tokens": 245,
  "output_tokens": 380,
  "estimated_cost": 0.0038,
  "provider": "openai",
  "model": "gpt-4o"
}
```

### `error`

A recoverable error occurred. The stream may continue after an error event.

```json
{
  "message": "Tool execution timed out after 300 seconds",
  "code": "TOOL_EXECUTION_ERROR"
}
```

### `done`

The stream is complete. Contains the full assistant message.

```json
{
  "assistant_data": {
    "id": "msg-uuid",
    "session_id": "session-uuid",
    "role": "assistant",
    "content": "Here are the files in your workspace...",
    "tool_calls": [...],
    "token_count": 380,
    "model_used": "gpt-4o",
    "created_at": "2026-03-02T12:00:01Z"
  }
}
```

---

## Agents

### `GET /agents`

List all non-hidden agents available in the registry.

**Response `200 OK`:**
```json
{
  "agents": [
    {
      "name": "default",
      "description": "Full-access coding agent with all tools enabled",
      "mode": "primary",
      "hidden": false,
      "native": true,
      "model": null,
      "temperature": null,
      "tools": [],
      "skills": [],
      "system_prompt": "You are a coding agent..."
    },
    {
      "name": "readonly",
      "description": "Analysis-only agent; write and execute tools disabled",
      "mode": "primary",
      "hidden": false,
      "native": true,
      "model": null,
      "temperature": null,
      "tools": [],
      "skills": [],
      "system_prompt": "You are a read-only analyst..."
    }
  ]
}
```

`mode` values: `primary`, `subagent`, `all`.  
`system_prompt` is truncated to 500 characters in the response.

**Response `503 Service Unavailable`:** Registry not yet initialized.

### `GET /agents/{name}`

Get a specific agent by name.

**Response `200 OK`:** Agent object  
**Response `404 Not Found`:** Agent not found or hidden  
**Response `503 Service Unavailable`:** Registry not yet initialized

---

## Tools

### `GET /tools`

List all registered tools.

**Response `200 OK`:**
```json
{
  "tools": [
    {
      "name": "bash",
      "source": "builtin",
      "module": "server.app.agent.tools"
    },
    {
      "name": "run_analysis",
      "source": "auto-discovered",
      "module": "myapp.tools.analysis"
    }
  ],
  "count": 2
}
```

### `GET /tools/{name}`

Get a specific tool by name.

**Response `200 OK`:** Tool object  
**Response `404 Not Found`**

### `GET /tools/errors`

Get any errors that occurred during tool discovery or reload.

**Response `200 OK`:**
```json
[
  {
    "module": "myapp.tools.broken",
    "error": "ImportError: No module named 'missing_dep'",
    "timestamp": "2026-03-02T12:00:00Z"
  }
]
```

### `POST /tools/reload`

Trigger a manual reload of tools from the discovery path (`.cognition/tools/`).

**Response `200 OK`:**
```json
{"tools_loaded": 5, "errors": 0}
```

---

## Models

### `GET /models`

List models from the catalog with optional filtering.

**Query parameters:**

| Parameter | Type | Description |
|---|---|---|
| `provider` | string | Filter by Cognition provider type (e.g. `openai`, `anthropic`) |
| `tool_call` | bool | Filter by tool call support |
| `q` | string | Search by model name or ID |

**Response `200 OK`:**
```json
{
  "models": [
    {
      "id": "gpt-4o",
      "provider": "openai",
      "display_name": "GPT-4o",
      "context_window": 128000,
      "output_limit": 16384,
      "capabilities": ["tool_call", "vision", "structured_output"],
      "input_cost": 2.5,
      "output_cost": 10.0,
      "modalities": {"input": ["text", "image"], "output": ["text"]},
      "family": "gpt",
      "status": null
    }
  ]
}
```

### `GET /models/providers`

List all provider configs from the ConfigRegistry.

### `GET /models/providers/{provider_id}/models`

List catalog models available for a specific provider config.

### `POST /models/providers`

Create a provider config.

**Request body:**
```json
{
  "id": "my-openai",
  "provider": "openai",
  "model": "gpt-4o",
  "api_key_env": "OPENAI_API_KEY",
  "enabled": true,
  "priority": 0
}
```

### `PATCH /models/providers/{provider_id}`

Update a provider config.

### `DELETE /models/providers/{provider_id}`

Delete a provider config.

### `POST /models/providers/{provider_id}/test`

Test provider connectivity and credentials.

**Response `200 OK`:**
```json
{
  "success": true,
  "provider": "openai",
  "model": "gpt-4o",
  "message": "Connection successful",
  "response_preview": "Hello!"
}
```

---

## Configuration

### `GET /config`

Get the current server configuration (infrastructure only). Secrets are redacted.

**Response `200 OK`:**
```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8000,
    "log_level": "info",
    "scoping_enabled": false
  },
  "llm": {
    "available_providers": [
      {"id": "openai", "name": "Openai", "models": ["gpt-4o", "gpt-4o-mini", "..."]}
    ]
  },
  "rate_limit": {
    "per_minute": 60,
    "burst": 10
  }
}
```

### `PATCH /config`

> **Note:** LLM and agent configuration is now managed via the ConfigRegistry API (`POST /models/providers`, `PATCH /agents/{name}`, etc.), not `PATCH /config`.

Update infrastructure configuration at runtime. Changes are persisted to `.cognition/config.yaml`.

**Allowed paths:** `rate_limit.per_minute`, `rate_limit.burst`, `observability.otel_enabled`, `observability.metrics_port`, `observability.otel_endpoint`, `mlflow.enabled`, `mlflow.experiment_name`.

**Request body:**
```json
{
  "rate_limit": {
    "per_minute": 120
  }
}
```

**Response `200 OK`:**
```json
{
  "updated": true,
  "changes": {"rate_limit.per_minute": 120},
  "backup_created": true,
  "timestamp": "2026-03-02T12:00:00Z"
}
```

**Response `422 Unprocessable Entity`:** Disallowed field or empty change set.

### `POST /config/rollback`

Roll back to the previous configuration backup.

**Response `200 OK`:**
```json
{"rolled_back": true, "timestamp": "2026-03-02T12:00:00Z"}
```

**Response `404 Not Found`:** No backup exists.

---

## Multi-Tenant Scoping

When `COGNITION_SCOPING_ENABLED=true`, all session endpoints require scope headers. The required headers are determined by `COGNITION_SCOPE_KEYS` (default: `["user"]`).

For `scope_keys: ["user", "project"]`:

```
X-Cognition-Scope-User: alice
X-Cognition-Scope-Project: proj-123
```

Missing required headers return `403 Forbidden`:

```json
{
  "error": "Missing required scope header: x-cognition-scope-user",
  "code": "PERMISSION_DENIED"
}
```

Sessions are automatically filtered to match the request's scope values. One tenant cannot read or write another tenant's sessions.

---

## Rate Limiting

Requests are throttled using a token bucket algorithm. When the limit is exceeded:

**Response `429 Too Many Requests`:**
```json
{
  "error": "Rate limit exceeded",
  "code": "RATE_LIMITED"
}
```

The `Retry-After` header indicates when the next request will be accepted.

Configure limits: `COGNITION_RATE_LIMIT_PER_MINUTE` (default: 60) and `COGNITION_RATE_LIMIT_BURST` (default: 10).

---

## Error Format

All error responses follow a consistent structure:

```json
{
  "error": "Human-readable message",
  "code": "ERROR_CODE",
  "details": {}
}
```

**Error codes:**

| Code | HTTP Status | Description |
|---|---|---|
| `NOT_FOUND` | 404 | Resource not found |
| `PERMISSION_DENIED` | 403 | Scope header missing or mismatch |
| `RATE_LIMITED` | 429 | Rate limit exceeded |
| `VALIDATION_ERROR` | 422 | Request body validation failed |
| `SESSION_NOT_FOUND` | 404 | Session ID does not exist |
| `LLM_UNAVAILABLE` | 503 | LLM provider configuration error or provider unreachable |
| `TOOL_EXECUTION_ERROR` | 500 | Tool raised an exception |
| `INTERNAL_ERROR` | 500 | Unexpected server error |
