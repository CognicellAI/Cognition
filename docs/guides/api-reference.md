# API Reference

Base URL: `http://localhost:8000`

All request and response bodies are JSON unless noted. Streaming endpoints return `text/event-stream`.

---

## Contents

- [Health & Readiness](#health--readiness)
- [Sessions](#sessions)
  - [`POST /sessions`](#post-sessions)
  - [`GET /sessions`](#get-sessions)
  - [`GET /sessions/{session_id}`](#get-sessionssession_id)
  - [`PATCH /sessions/{session_id}`](#patch-sessionssession_id)
  - [`DELETE /sessions/{session_id}`](#delete-sessionssession_id)
  - [`POST /sessions/{session_id}/abort`](#post-sessionssession_idabort)
- [Messages](#messages)
  - [`POST /sessions/{session_id}/messages`](#post-sessionssession_idmessages)
  - [`GET /sessions/{session_id}/messages`](#get-sessionssession_idmessages)
  - [`GET /sessions/{session_id}/messages/{message_id}`](#get-sessionssession_idmessagesmessage_id)
- [SSE Event Types](#sse-event-types)
  - [`token`](#token)
  - [`tool_call`](#tool_call)
  - [`tool_result`](#tool_result)
  - [`planning`](#planning)
  - [`step_complete`](#step_complete)
  - [`delegation`](#delegation)
  - [`status`](#status)
  - [`usage`](#usage)
  - [`error`](#error)
  - [`done`](#done)
- [Agents](#agents)
  - [`GET /agents`](#get-agents)
  - [`GET /agents/{name}`](#get-agentsname)
  - [`POST /agents`](#post-agents)
  - [`PUT /agents/{name}`](#put-agentsname)
  - [`PATCH /agents/{name}`](#patch-agentsname)
  - [`DELETE /agents/{name}`](#delete-agentsname)
- [Skills](#skills)
  - [`GET /skills`](#get-skills)
  - [`GET /skills/{name}`](#get-skillsname)
  - [`POST /skills`](#post-skills)
  - [`PUT /skills/{name}`](#put-skillsname)
  - [`PATCH /skills/{name}`](#patch-skillsname)
  - [`DELETE /skills/{name}`](#delete-skillsname)
- [Tools](#tools)
  - [`GET /tools`](#get-tools)
  - [`GET /tools/{name}`](#get-toolsname)
  - [`GET /tools/errors`](#get-toolserrors)
  - [`POST /tools`](#post-tools)
  - [`DELETE /tools/{name}`](#delete-toolsname)
  - [`POST /tools/reload`](#post-toolsreload)
- [Models](#models)
  - [`GET /models`](#get-models)
  - [`GET /models/providers`](#get-modelsproviders)
  - [`GET /models/providers/{provider_id}/models`](#get-modelsprovidersprovider_idmodels)
  - [`POST /models/providers`](#post-modelsproviders)
  - [`PATCH /models/providers/{provider_id}`](#patch-modelsprovidersprovider_id)
  - [`DELETE /models/providers/{provider_id}`](#delete-modelsprovidersprovider_id)
  - [`POST /models/providers/{provider_id}/test`](#post-modelsprovidersprovider_idtest)
- [Configuration](#configuration)
  - [`GET /config`](#get-config)
  - [`PATCH /config`](#patch-config)
  - [`POST /config/rollback`](#post-configrollback)
- [Multi-Tenant Scoping](#multi-tenant-scoping)
- [Rate Limiting](#rate-limiting)
- [Error Format](#error-format)

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
| `agent_name` | string | No | Agent to bind; must be a known `primary` or `all` agent. Defaults to `"default"`. Returns `422` if name is unknown. |

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

**Response `422 Unprocessable Entity`:** `agent_name` is not a known primary agent.

### `GET /sessions`

List sessions for the current workspace. Filtered by scope when scoping is enabled.

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
    "max_tokens": 2048,
    "recursion_limit": 500
  }
}
```

**`SessionConfig` fields:**

| Field | Type | Description |
|---|---|---|
| `provider_id` | string | Reference a specific `ProviderConfig` by ID from ConfigRegistry. Takes priority over `provider`/`model`. |
| `provider` | string | Provider type override: `openai`, `anthropic`, `bedrock`, `openai_compatible`, `google_genai`, `google_vertexai` |
| `model` | string | Model ID override |
| `temperature` | float | Temperature (0.0–2.0) |
| `max_tokens` | int | Max output tokens |
| `recursion_limit` | int | Max agent ReAct loop depth |

**Provider resolution priority** (highest to lowest): `provider_id` → `provider`+`model` → `AgentDefinition.config` → first enabled ProviderConfig from ConfigRegistry.

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
| `content` | string (min 1) | Yes | The user's message |
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

The agent is invoking a tool. `id` correlates with the `tool_call_id` in the subsequent `tool_result`.

```json
{
  "name": "bash",
  "args": {"command": "ls -la"},
  "id": "call_abc123"
}
```

### `tool_result`

Result of a tool invocation. `tool_call_id` matches the `id` in the preceding `tool_call`.

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
  "from_agent": "main",
  "to_agent": "security-auditor",
  "task": "call_abc123"
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

A recoverable error occurred. The stream terminates after an error event.

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

### `POST /agents`

Create or replace an agent definition in the ConfigRegistry.

**Request body:**
```json
{
  "name": "security-auditor",
  "system_prompt": "You are a security expert. Audit code for vulnerabilities.",
  "description": "Audits code for security issues",
  "mode": "subagent",
  "tools": ["myapp.tools.security.run_semgrep"],
  "skills": [],
  "memory": ["AGENTS.md"],
  "interrupt_on": {},
  "model": "gpt-4o",
  "temperature": 0.1,
  "scope": {}
}
```

| Field | Type | Description |
|---|---|---|
| `name` | string | Agent identifier (1–100 chars) |
| `system_prompt` | string | Agent's system prompt |
| `description` | string | Human-readable description |
| `mode` | `"primary"` \| `"subagent"` \| `"all"` | Whether agent can own sessions, be delegated to, or both |
| `tools` | list[string] | Dotted Python import paths for tool functions |
| `skills` | list[string] | Paths to SKILL.md files or directories |
| `memory` | list[string] | Paths to instruction files (e.g. AGENTS.md) |
| `interrupt_on` | dict | Tool names mapped to `true` for HITL confirmation |
| `model` | string | Model override (overrides global default for this agent's sessions) |
| `temperature` | float | Temperature override |
| `scope` | dict | Scope restriction; empty `{}` = global |

**Response `201 Created`:** Agent object  
**Response `422 Unprocessable Entity`:** Validation error

### `PUT /agents/{name}`

Replace an agent definition entirely.

**Request body:** Same as `POST /agents`  
**Response `200 OK`:** Updated agent object  
**Response `404 Not Found`**

### `PATCH /agents/{name}`

Partially update an agent definition. Only provided fields are changed.

**Request body (all fields optional):**
```json
{
  "system_prompt": "Updated prompt.",
  "model": "claude-sonnet-4-6",
  "temperature": 0.5
}
```

**Response `200 OK`:** Updated agent object  
**Response `404 Not Found`**

### `DELETE /agents/{name}`

Delete an agent definition from the ConfigRegistry.

**Response `204 No Content`**  
**Response `404 Not Found`:** Agent not found  
**Response `400 Bad Request`:** Attempt to delete a built-in (native) agent

---

## Skills

Skills are SKILL.md files stored in the ConfigRegistry. When an agent loads, its configured skills are injected progressively as the context window fills.

### `GET /skills`

List all registered skills.

**Query parameters:**

| Parameter | Type | Description |
|---|---|---|
| `scope` | dict (via headers) | Filtered by scope when scoping is enabled |

**Response `200 OK`:**
```json
{
  "skills": [
    {
      "name": "python-testing",
      "path": "/skills/api/python-testing",
      "enabled": true,
      "description": "pytest patterns and fixtures",
      "content": "# Python Testing\n\n...",
      "scope": {},
      "source": "api"
    }
  ],
  "count": 1
}
```

### `GET /skills/{name}`

Get a specific skill by name, including full content.

**Response `200 OK`:** Skill object  
**Response `404 Not Found`**

### `POST /skills`

Create or replace a skill in the ConfigRegistry.

**Request body:**
```json
{
  "name": "python-testing",
  "content": "# Python Testing\n\nUse pytest. Write tests in tests/. Run with `pytest`.",
  "description": "pytest patterns for this project",
  "enabled": true,
  "scope": {}
}
```

| Field | Type | Description |
|---|---|---|
| `name` | string | Skill identifier (1–100 chars) |
| `content` | string | Full SKILL.md content (YAML frontmatter + Markdown body) |
| `path` | string | Filesystem path alternative to inline content |
| `description` | string | Short description |
| `enabled` | bool | Whether this skill is active (default `true`) |
| `scope` | dict | Scope restriction; empty `{}` = global |

**Response `201 Created`:** Skill object  
**Response `422 Unprocessable Entity`:** Validation error

### `PUT /skills/{name}`

Replace a skill entirely.

**Request body:** Same as `POST /skills`  
**Response `200 OK`:** Updated skill object  
**Response `404 Not Found`**

### `PATCH /skills/{name}`

Partially update a skill. Only provided fields are changed.

**Request body (all fields optional):**
```json
{
  "content": "# Updated content...",
  "enabled": false
}
```

**Response `200 OK`:** Updated skill object  
**Response `404 Not Found`**

### `DELETE /skills/{name}`

Delete a skill from the ConfigRegistry.

**Response `204 No Content`**  
**Response `404 Not Found`**

---

## Tools

### `GET /tools`

List all registered tools from both file discovery (AgentRegistry) and API registration (ConfigRegistry).

**Response `200 OK`:**
```json
{
  "tools": [
    {
      "name": "bash",
      "source_type": "file",
      "source": "file",
      "module": "server.app.agent.tools",
      "description": null,
      "enabled": true
    },
    {
      "name": "search-jira",
      "source_type": "api_code",
      "source": "api_code",
      "module": null,
      "description": "Search Jira issues",
      "enabled": true
    },
    {
      "name": "run_analysis",
      "source_type": "api_path",
      "source": "api_path",
      "module": "myapp.tools.analysis",
      "description": null,
      "enabled": true
    }
  ],
  "count": 3
}
```

`source_type` values:
- `"file"` — auto-discovered from `.cognition/tools/` or built-in
- `"api_code"` — registered via `POST /tools` with `code` field (Python source stored in DB)
- `"api_path"` — registered via `POST /tools` with `path` field (module path)

### `GET /tools/{name}`

Get a specific tool by name. Checks file-discovered tools first, then ConfigRegistry.

**Response `200 OK`:** Tool object  
**Response `404 Not Found`**

### `GET /tools/errors`

Get any errors that occurred during tool discovery or reload.

**Response `200 OK`:**
```json
[
  {
    "file": ".cognition/tools/broken_tool.py",
    "error_type": "ImportError",
    "error": "No module named 'missing_dep'",
    "timestamp": 1711972800.0
  }
]
```

### `POST /tools`

Register a tool in the ConfigRegistry. Exactly one of `code` or `path` must be provided.

**Request body — inline source code:**
```json
{
  "name": "search-jira",
  "code": "from langchain_core.tools import tool\n\n@tool\ndef search_jira(query: str) -> str:\n    \"\"\"Search Jira issues by query string.\"\"\"\n    ...",
  "enabled": true,
  "description": "Search Jira issues",
  "scope": {}
}
```

**Request body — module path:**
```json
{
  "name": "jira-tools",
  "path": "mycompany.cognition_tools.jira",
  "enabled": true
}
```

| Field | Type | Description |
|---|---|---|
| `name` | string | Tool identifier (1–100 chars) |
| `code` | string | Full Python source containing `@tool`-decorated functions or `BaseTool` subclasses |
| `path` | string | Dotted module path importable by the server process |
| `enabled` | bool | Whether this tool is active (default `true`) |
| `description` | string | Optional description |
| `scope` | dict | Scope restriction; empty `{}` = global |

**Response `201 Created`:** Tool object with `source_type`  
**Response `422 Unprocessable Entity`:** Neither `code` nor `path` provided; or both provided

> **Security:** Tool code executes with full Python privileges inside the sandbox backend. Restrict this endpoint to authorized administrators at the Gateway/proxy layer.

### `DELETE /tools/{name}`

Remove an API-registered tool from the ConfigRegistry.

**Response `204 No Content`**  
**Response `404 Not Found`:** Tool not in ConfigRegistry

### `POST /tools/reload`

Trigger a manual reload of file-discovered tools from `.cognition/tools/`.

**Response `200 OK`:**
```json
{"tools_loaded": 5, "errors": 0}
```

---

## Models

### `GET /models`

List models from the models.dev catalog with optional filtering.

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

`status` values: `null` (active), `"deprecated"`, `"beta"`.

### `GET /models/providers`

List all provider configs from the ConfigRegistry.

**Response `200 OK`:**
```json
{
  "providers": [
    {
      "id": "default",
      "provider": "openai_compatible",
      "model": "google/gemini-3-flash-preview",
      "display_name": null,
      "enabled": true,
      "priority": 1,
      "max_retries": 2,
      "api_key_env": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
      "base_url": "https://openrouter.ai/api/v1",
      "region": null,
      "role_arn": null,
      "extra": {},
      "scope": {},
      "source": "api"
    }
  ],
  "count": 1
}
```

### `GET /models/providers/{provider_id}/models`

List catalog models available for a specific provider config.

**Response `200 OK`:** Same schema as `GET /models`

### `POST /models/providers`

Create a provider config in the ConfigRegistry. Takes effect immediately — no restart required.

**Request body:**
```json
{
  "id": "my-openai",
  "provider": "openai",
  "model": "gpt-4o",
  "api_key_env": "OPENAI_API_KEY",
  "enabled": true,
  "priority": 0,
  "max_retries": 2,
  "base_url": null,
  "region": null,
  "role_arn": null,
  "scope": {}
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | Yes | Unique identifier for this config |
| `provider` | string | Yes | `openai`, `anthropic`, `bedrock`, `openai_compatible`, `google_genai`, `google_vertexai` |
| `model` | string | Yes | Model ID |
| `api_key_env` | string | No | Name of the env var holding the API key (not the key itself) |
| `enabled` | bool | No | Default `true` |
| `priority` | int | No | Lower = higher priority in resolution chain. Default `0` |
| `max_retries` | int | No | Retry attempts on 429/5xx. Default `2` |
| `base_url` | string | No | Required for `openai_compatible` |
| `region` | string | No | AWS region for `bedrock` |
| `role_arn` | string | No | IAM role ARN for Bedrock cross-account access |
| `scope` | dict | No | Scope restriction; empty `{}` = global |

**Response `201 Created`:** Provider config object  
**Response `422 Unprocessable Entity`:** Validation error

### `PATCH /models/providers/{provider_id}`

Partially update a provider config.

**Request body (all fields optional):**
```json
{
  "model": "gpt-4o-mini",
  "enabled": false,
  "priority": 10
}
```

**Response `200 OK`:** Updated provider config  
**Response `404 Not Found`**

### `DELETE /models/providers/{provider_id}`

Delete a provider config.

**Response `204 No Content`**  
**Response `404 Not Found`**

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

**Response `200 OK` (failure):**
```json
{
  "success": false,
  "provider": "openai",
  "model": "gpt-4o",
  "message": "AuthenticationError: Invalid API key",
  "response_preview": null
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

> **Note:** LLM and agent configuration is managed via the ConfigRegistry API (`POST /models/providers`, `PATCH /agents/{name}`, etc.), not `PATCH /config`.

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
| `STREAMING_ERROR` | 500 | Error during agent streaming |
| `ABORTED` | — | Stream aborted via `POST /sessions/{id}/abort` (delivered as SSE `error` event) |
| `INTERNAL_ERROR` | 500 | Unexpected server error |
