# API Reference

Base URL: `http://localhost:8000`

All request and response bodies are JSON unless noted. Streaming endpoints return `text/event-stream`.

---

## Contents

- [Health & Readiness](#health-readiness)
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
  - [`planning`](#planning) *(reserved)*
  - [`step_complete`](#step_complete) *(reserved)*
  - [`delegation`](#delegation)
  - [`status`](#status)
  - [`interrupt`](#interrupt)
  - [`usage`](#usage)
  - [`sandbox_lifecycle`](#sandbox_lifecycle)
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
- [MCP Servers](#mcp-servers)
  - [`GET /mcp-servers`](#get-mcp-servers)
  - [`POST /mcp-servers`](#post-mcp-servers)
  - [`GET /mcp-servers/{name}`](#get-mcp-serversname)
  - [`PATCH /mcp-servers/{name}`](#patch-mcp-serversname)
  - [`DELETE /mcp-servers/{name}`](#delete-mcp-serversname)
- [Sandbox Profiles](#sandbox-profiles)
  - [`GET /sandbox/profiles`](#get-sandboxprofiles)
  - [`POST /sandbox/profiles`](#post-sandboxprofiles)
  - [`GET /sandbox/profiles/{name}`](#get-sandboxprofilesname)
  - [`PATCH /sandbox/profiles/{name}`](#patch-sandboxprofilesname)
  - [`DELETE /sandbox/profiles/{name}`](#delete-sandboxprofilesname)
- [Artifacts](#artifacts)
  - [`GET /artifacts`](#get-artifacts)
  - [`POST /artifacts`](#post-artifacts)
  - [`GET /artifacts/{artifact_id}`](#get-artifactsartifact_id)
  - [`PUT /artifacts/{artifact_id}`](#put-artifactsartifact_id)
  - [`DELETE /artifacts/{artifact_id}`](#delete-artifactsartifact_id)
  - [`GET /artifacts/{artifact_id}/versions`](#get-artifactsartifact_idversions)
- [Capabilities](#capabilities)
  - [`GET /capabilities`](#get-capabilities)
- [A2A Protocol](#a2a-protocol)
  - [`GET /.well-known/agent-card.json`](#get-well-knownagent-cardjson)
  - [`POST /a2a/{agent_name}`](#post-a2aagent_name)
  - [Builder-Defined Runtime Scoping](#builder-defined-runtime-scoping)
- [Rate Limiting](#rate-limiting)
- [Error Format](#error-format)

---

## Health & Readiness

### `GET /health`

Returns server health status.

`active_sessions` counts open non-terminal sessions, including idle sessions
that can still accept follow-up messages.

**Response `200 OK`:**
```json
{
  "status": "healthy",
  "version": "0.10.0",
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

Create a new reusable conversation session. Each message sent to the session
creates a run on the session's `thread_id`; successful run completion returns
the session to `idle` so the same session can receive follow-up messages.

**Request body:**
```json
{
  "title": "My session",
  "agent_name": "default",
  "metadata": {
    "repository": "myorg/myrepo",
    "pr_number": "42"
  }
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `title` | string (max 200) | No | Human-readable label |
| `agent_name` | string | No | Agent to bind; must be a known `primary` or `all` agent. Defaults to `"default"`. Returns `422` if name is unknown. |
| `metadata` | object | No | Arbitrary builder-defined flat key-value metadata attached to the session |

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
  "status": "idle",
  "agent_name": "default",
  "metadata": {"repository": "myorg/myrepo", "pr_number": "42"},
  "created_at": "2026-03-02T12:00:00Z",
  "updated_at": "2026-03-02T12:00:00Z",
  "message_count": 0
}
```

**Response `422 Unprocessable Entity`:** `agent_name` is not a known primary agent in the request scope.

Session status summary:

| Status | Meaning |
|---|---|
| `idle` | No run is active; the session can accept a follow-up message |
| `active` | A run is currently executing |
| `waiting_for_approval` | A run is paused for human-in-the-loop review; use `POST /sessions/{session_id}/resume` |
| `aborted`, `failed`, `done`, `expired` | Terminal session states; create a new session for more work |

### `GET /sessions`

List sessions for the current workspace. Filtered by scope when scoping is enabled.

Metadata filtering is supported with query parameters of the form `metadata.<key>=<value>`.

Examples:

```bash
curl "http://localhost:8000/sessions?metadata.repository=myorg/myrepo"
curl "http://localhost:8000/sessions?metadata.repository=myorg/myrepo&metadata.pr_number=42"
```

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
  "metadata": {
    "repository": "myorg/myrepo",
    "pr_number": "43"
  },
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

**Validation rules:**

- `config.provider_id` selects an exact configured provider row.
- `config.provider` requires `config.model`.
- `config.model` without `config.provider` or `config.provider_id` is only accepted when exactly one enabled provider type matches that model.
- If no enabled provider matches the model, Cognition returns `422`.
- If multiple enabled provider types match the model, Cognition returns `422` and asks the caller to specify `provider` or `provider_id`.

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

### `POST /sessions/{session_id}/resume`

Resume an interrupted HITL run after an `interrupt` SSE event.

Use this endpoint only while the session is in `waiting_for_approval`. A normal
completed message run returns the session to `idle`; send another
`POST /sessions/{session_id}/messages` request to continue that conversation.

**Request body:**
```json
{
  "decision": "approve",
  "tool_call_id": "call_abc123",
  "tool_name": "write_file",
  "args": null
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `decision` | `approve` \| `edit` \| `reject` | Yes | Human decision for the interrupted tool call |
| `tool_call_id` | string | Yes | Interrupted tool call identifier |
| `tool_name` | string | Yes | Interrupted tool name |
| `args` | object | No | Replacement tool args when `decision="edit"`; may include a rejection message for `reject` |

If `Accept: text/event-stream` is sent, Cognition streams the resumed
continuation. Otherwise it returns a simple success response. When the resumed
run completes successfully, the session returns to `idle`.

**Response `200 OK` (JSON):**
```json
{"success": true, "message": "Session resumed"}
```

**Response `409 Conflict`:** Session is not in `waiting_for_approval` state.

---

## Messages

### `POST /sessions/{session_id}/messages`

Send a user message and receive the agent's streaming response via Server-Sent Events.

**Request body:**
```json
{
  "content": "List the files in the workspace.",
  "model": "gpt-4o-mini",
  "callback_url": "https://example.com/cognition-callback"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `content` | string (min 1) | Yes | The user's message |
| `model` | string | No | Override model for this message only |
| `parent_id` | string | No | Parent message ID for threaded context |
| `callback_url` | URL | No | Best-effort completion callback URL for a final POST after the run finishes |

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
      "role": "ROLE_USER",
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

The agent has created a task plan.

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

Values: `thinking`, `idle`, `waiting_for_approval`, `resuming`, `cancelled`.

### `interrupt`

The agent is paused waiting for human approval before executing a protected tool.

```json
{
  "tool_call_id": "call_abc123",
  "tool_name": "write_file",
  "args": {"file_path": "notes.txt", "content": "hello"},
  "session_id": "session-uuid",
  "action_requests": [
    {
      "name": "write_file",
      "args": {"file_path": "notes.txt", "content": "hello"},
      "review_config": {"action_name": "write_file", "allowed_decisions": ["approve", "edit", "reject"]}
    }
  ]
}
```

Use this payload with `POST /sessions/{session_id}/resume`.

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

### `sandbox_lifecycle`

Sandbox backend lifecycle transition. Lambda MicroVM snapshots include
token-free runtime metadata.

```json
{
  "sandbox_id": "microvm-123",
  "phase": "runtime_snapshot",
  "sandbox_backend": "aws_lambda_microvm",
  "duration_ms": null,
  "exit_code": null,
  "is_warm_pool_hit": false,
  "metadata": {
    "microvm_id": "microvm-123",
    "endpoint": "https://example.lambda-url.aws",
    "profile": "default-lambda",
    "image": "arn:aws:lambda:us-west-2:123456789012:microvm-image:cognition-runtime",
    "image_version": "1.0",
    "status": "RUNNING",
    "region": "us-west-2",
    "port": 8080,
    "maximum_duration_seconds": 3600,
    "logging_mode": "disabled",
    "quota": {
      "max_concurrent_sessions": 10,
      "max_session_starts_per_minute": 30
    },
    "execution_role_fingerprint": "abcd1234ef567890",
    "correlation": {
      "session_id": "session-123",
      "run_id": "run-123",
      "agent_name": "repo-maintainer",
      "profile": "default-lambda",
      "scope_keys": ["project", "tenant"],
      "scope_fingerprint": "0123456789abcdef"
    }
  }
}
```

Proxy auth tokens and credentials are filtered before lifecycle events are
streamed or persisted.

### `error`

A recoverable error occurred. The stream terminates after an error event.

```json
{
  "message": "Tool execution timed out after 300 seconds",
  "code": "TOOL_EXECUTION_ERROR"
}
```

### `done`

The current run's stream is complete. Contains the full assistant message.
The session remains reusable unless it has moved to a terminal session state.

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
      "display_name": null,
      "description": "Full-access coding agent with all tools enabled",
      "mode": "primary",
      "hidden": false,
      "native": true,
      "a2a": {
        "exposed": false,
        "public_interface_url": null,
        "default_input_modes": ["text/plain", "application/json"],
        "default_output_modes": ["text/plain", "application/json"],
        "skills": []
      },
      "provider": null,
      "model": null,
      "temperature": null,
      "config": {
        "temperature": null,
        "max_tokens": null,
        "recursion_limit": null,
        "tool_token_limit_before_evict": null,
        "context_policy": null,
        "excluded_tools": [],
        "blocked_tools": [],
        "provider": null,
        "model": null,
        "timeout_seconds": null
      },
      "response_format": null,
      "interrupt_on": {},
      "permissions": [],
      "tools": [],
      "skills": [],
      "sandbox_profile": null,
      "sandbox_execution_role_arn": null,
      "subagents": [],
      "async_subagents": [],
      "system_prompt": "You are a coding agent..."
    }
  ]
}
```

`mode` values: `primary`, `subagent`, `all`.  
`system_prompt` is returned as stored.

**Response `503 Service Unavailable`:** Registry not yet initialized.

### `GET /agents/{name}`

Get a specific agent by name.

**Response `200 OK`:** Agent object  
**Response `404 Not Found`:** Agent not found or hidden  
**Response `503 Service Unavailable`:** Registry not yet initialized

### Agent Tool Policy Fields

Agent create/update requests use top-level `excluded_tools` and `blocked_tools` fields.
Read responses return those values under `config.excluded_tools` and `config.blocked_tools`.

The two fields are intentionally separate:

| Field | Runtime effect | When to use |
|---|---|---|
| `excluded_tools` | Removes matching tool names from the model-visible tool schema before the model can select them. | Hide inherited Deep Agents harness tools for a specific agent, such as a customer-facing concierge that should not see `grep`, `ls`, `execute`, or `websearch`. |
| `blocked_tools` | Denies matching tool calls at execution time through `ToolSecurityMiddleware`. The tool may still be visible unless it is also excluded. | Enforce a call-time safety guard. Per-agent values are merged with deployment-wide `COGNITION_BLOCKED_TOOLS`. |

Use both fields for the same tool name when you want no model affordance plus a runtime guard. Tool names must match the runtime tool name exactly.

### `POST /agents`

Create or replace an agent definition in the ConfigRegistry.

**Request body:**
```json
{
  "name": "security-auditor",
  "display_name": "Security Auditor",
  "system_prompt": "You are a security expert. Audit code for vulnerabilities.",
  "description": "Audits code for security issues",
  "mode": "subagent",
  "tools": ["run_semgrep"],
  "skills": ["python-review"],
  "memory": ["AGENTS.md"],
  "interrupt_on": {},
  "a2a": {
    "exposed": false,
    "public_interface_url": "https://agents.example.com/security-auditor/a2a",
    "default_input_modes": ["text/plain", "application/json"],
    "default_output_modes": ["text/plain", "application/json"],
    "skills": []
  },
  "model": "gpt-4o",
  "temperature": 0.1,
  "max_tokens": 4096,
  "recursion_limit": 100,
  "excluded_tools": ["glob", "grep", "ls"],
  "blocked_tools": ["execute"],
  "sandbox_profile": "default-lambda",
  "sandbox_execution_role_arn": "arn:aws:iam::123456789012:role/security-auditor-runtime",
  "scope": {}
}
```

| Field | Type | Description |
|---|---|---|
| `name` | string | Agent identifier (1–100 chars) |
| `display_name` | string | Optional human-readable name used for public Agent presentation without changing runtime lookup |
| `system_prompt` | string | Agent's system prompt |
| `description` | string | Human-readable description |
| `mode` | `"primary"` \| `"subagent"` \| `"all"` | Whether agent can own sessions, be delegated to, or both |
| `hidden` | boolean | Hide the agent from `GET /agents` list results |
| `a2a` | object | A2A exposure and public Agent Card presentation. See the [A2A Builder Guide](a2a.md). |
| `tools` | list[string] | Registry tool names to attach to this agent |
| `skills` | list[string] | Registry skill names to attach to this agent |
| `memory` | list[string] | Paths to instruction files (e.g. AGENTS.md) |
| `interrupt_on` | dict | Tool names mapped to `true` for HITL confirmation |
| `permissions` | list[object] | Deep Agents filesystem permission rules |
| `response_format` | string | Dotted path to a structured output schema |
| `provider` | string | Provider type override used with `model` |
| `model` | string | Model override for this agent's sessions |
| `temperature` | float | Temperature override |
| `max_tokens` | int | Max output tokens |
| `recursion_limit` | int | Max agent loop depth |
| `tool_token_limit_before_evict` | int | Token threshold for evicting large tool output |
| `context_policy` | object | Context and summarization policy configuration |
| `excluded_tools` | list[string] | Tool names removed from the model-visible tool list for this agent |
| `blocked_tools` | list[string] | Tool names denied at execution time for this agent in addition to global `COGNITION_BLOCKED_TOOLS` |
| `timeout_seconds` | float | Per-agent model request timeout |
| `middleware` | list | Middleware names or middleware config dicts |
| `subagents` | list[object] | In-process subagent definitions |
| `async_subagents` | list[object] | Experimental remote Agent Protocol async subagent definitions |
| `sandbox_profile` | string | Trusted sandbox profile selected for this agent |
| `sandbox_execution_role_arn` | string | Trusted IAM role ARN assigned to this agent's sandbox runtime |
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
Send an empty list to clear `excluded_tools` or `blocked_tools`. Omit the field to leave the current policy unchanged.

**Request body (all fields optional):**
```json
{
  "system_prompt": "Updated prompt.",
  "model": "claude-sonnet-4-6",
  "temperature": 0.5,
  "excluded_tools": ["glob", "grep", "ls"],
  "blocked_tools": ["execute"],
  "sandbox_profile": "default-lambda"
}
```

**Response `200 OK`:** Updated agent object  
**Response `404 Not Found`**

For scoped agents, use the same `X-Cognition-Scope-*` headers used to create or read the agent. The response is reloaded from the matched scoped row after the update.

### `DELETE /agents/{name}`

Delete an agent definition from the ConfigRegistry.

**Response `204 No Content`**  
**Response `404 Not Found`:** Agent not found  
**Response `400 Bad Request`:** Attempt to delete a built-in (native) agent

---

## Skills

Skills are SKILL.md files stored in the ConfigRegistry. When an agent loads, its configured skills are injected progressively as the context window fills.

File-managed skills (seeded from `skill_sources` directories at startup) have `source: "file"` and cannot be modified or deleted via the API (returns `409 Conflict`). API-created skills have `source: "api"`.

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
      "enabled": true,
      "interrupt_on": false
    },
    {
      "name": "search-jira",
      "source_type": "api_code",
      "source": "api_code",
      "module": null,
      "description": "Search Jira issues",
      "enabled": true,
      "interrupt_on": true
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

File-managed tools (seeded from `tool_sources` directories at startup) have `source: "file"` and cannot be modified or deleted via the API (returns `409 Conflict`).

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
| `interrupt_on` | bool | Whether this tool should require approval by default in builders that consume tool metadata |
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

Provider configs are the canonical model selection surface in Cognition. Cognition validates the provider config, resolves credentials and transport settings, builds a concrete LangChain chat model, and passes that model into Deep Agents.

### Provider Types And Validation

| Provider type | Required fields | Notes |
|---|---|---|
| `openai` | `id`, `provider`, `model` | Uses `OPENAI_API_KEY` unless `api_key_env` overrides it |
| `anthropic` | `id`, `provider`, `model` | Uses `ANTHROPIC_API_KEY` unless `api_key_env` overrides it |
| `bedrock` | `id`, `provider`, `model`, `region` | `role_arn` is allowed only for `bedrock` |
| `openai_compatible` | `id`, `provider`, `model`, `base_url` | Covers OpenRouter, Ollama, vLLM, LiteLLM, LM Studio, and similar endpoints |
| `google_genai` | `id`, `provider`, `model` | Uses `GOOGLE_API_KEY` unless `api_key_env` overrides it |
| `google_vertexai` | `id`, `provider`, `model` | Vertex runtime details come from ADC and project config |
| `mock` | `id`, `provider`, `model` | Test-only provider |

Validation rules enforced by Cognition:

- `base_url` is required for `openai_compatible`
- `base_url` is rejected for non-`openai_compatible` providers
- `region` is required for `bedrock`
- `region` is rejected for non-`bedrock` providers
- `role_arn` is rejected for non-`bedrock` providers

### `GET /models`

List models from the models.dev catalog with optional filtering.

This endpoint returns catalog metadata only for provider types that have at least one enabled provider config visible in the current scope. It does not expose the full global models.dev catalog.

**Query parameters:**

| Parameter | Type | Description |
|---|---|---|
| `provider` | string | Filter by Cognition provider type (e.g. `openai`, `anthropic`) |
| `tool_call` | bool | Filter by tool call support |
| `q` | string | Search by model name or ID |

Notes:

- if no enabled providers are configured in scope, the response is empty
- if `provider` is requested but no matching enabled provider config exists in scope, the response is empty
- `openai_compatible` providers do not contribute catalog entries unless Cognition has an explicit catalog mapping for the upstream service

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

Use this endpoint to inspect the effective provider registry before binding sessions by `provider_id`.

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

For `openai_compatible` providers, the catalog may be incomplete because available models depend on the upstream service.

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
| `max_retries` | int | No | Stored but **not yet enforced** — field is accepted and persisted but not passed to the LLM client. Default `2` |
| `base_url` | string | No | Required for `openai_compatible` |
| `region` | string | No | AWS region for `bedrock` |
| `role_arn` | string | No | IAM role ARN for Bedrock cross-account access |
| `scope` | dict | No | Scope restriction; empty `{}` = global |

**Response `201 Created`:** Provider config object  
**Response `422 Unprocessable Entity`:** Validation error

Preferred usage:

- use the returned `id` as `SessionConfig.provider_id`
- prefer `provider_id` over model-only session selection

Common invalid configs:

- `openai_compatible` without `base_url`
- `bedrock` without `region`
- `region` or `role_arn` on non-`bedrock` providers
- `base_url` on non-`openai_compatible` providers

### `PATCH /models/providers/{provider_id}`

Partially update a provider config.

Updates are fully revalidated, so an invalid partial update is rejected instead of leaving the provider in a broken state.

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

Validate that the configured provider can be resolved and instantiated successfully.

Use this endpoint during onboarding before binding sessions to a new `provider_id`.

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

## MCP Servers

Manage remote MCP (Model Context Protocol) tool servers at runtime. File-managed servers (from `.cognition/config.yaml`) have `source: "file"` and cannot be modified via the API (returns `409 Conflict`).

### `GET /mcp-servers`

List all registered MCP servers visible in the current scope.

**Response `200 OK`:**
```json
{
  "servers": [
    {
      "name": "github-tools",
      "url": "https://mcp.github.example.com/sse",
      "headers": {},
      "enabled": true,
      "transport": "sse",
      "scope": {},
      "source": "api"
    }
  ],
  "count": 1
}
```

> **Note:** `headers` is always returned as an empty `{}` to prevent credential leakage.

### `POST /mcp-servers`

Register a new MCP server.

**Request body:**
```json
{
  "name": "my-tools",
  "url": "https://tools.example.com/sse",
  "transport": "sse",
  "enabled": true,
  "headers": {"Authorization": "Bearer ..."},
  "scope": {}
}
```

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | string (1–100) | Yes | — | Unique server identifier |
| `url` | string | Yes | — | HTTP/HTTPS URL (stdio not supported) |
| `transport` | `"sse"` \| `"streamable_http"` | No | `"sse"` | Transport protocol |
| `enabled` | bool | No | `true` | Whether to connect |
| `headers` | dict | No | `{}` | HTTP headers sent with requests |
| `scope` | dict | No | `{}` | Scope restriction |

**Response `201 Created`:** MCP server object  
**Response `422 Unprocessable Entity`:** Validation error

### `GET /mcp-servers/{name}`

Get a specific MCP server by name.

**Response `200 OK`:** MCP server object  
**Response `404 Not Found`**

### `PATCH /mcp-servers/{name}`

Partially update an MCP server.

**Request body (all fields optional):**
```json
{
  "enabled": false,
  "url": "https://new-url.example.com/sse"
}
```

**Response `200 OK`:** Updated MCP server object  
**Response `404 Not Found`**  
**Response `409 Conflict`:** Server is file-managed

### `DELETE /mcp-servers/{name}`

Delete an MCP server.

**Response `204 No Content`**  
**Response `404 Not Found`**  
**Response `409 Conflict`:** Server is file-managed

---

## Sandbox Profiles

Manage AWS Lambda MicroVM sandbox profiles at runtime. File-managed profiles
from `.cognition/config.yaml` have `source: "file"` and cannot be modified or
deleted via the API.

### `GET /sandbox/profiles`

List sandbox profiles visible in the current scope.

**Response `200 OK`:**
```json
{
  "profiles": [
    {
      "name": "default-lambda",
      "backend": "aws_lambda_microvm",
      "image_arn": "arn:aws:lambda:us-west-2:123456789012:microvm-image:cognition-runtime",
      "image_version": "1.0",
      "region": "us-west-2",
      "ingress_network_connector_arns": [],
      "egress_mode": "internet",
      "egress_network_connector_arns": [],
      "idle_policy": {
        "max_idle_duration_seconds": 900,
        "suspended_duration_seconds": 300,
        "auto_resume_enabled": true
      },
      "logging": {
        "disabled": {},
        "cloud_watch": null
      },
      "quota": {
        "max_concurrent_sessions": 10,
        "max_session_starts_per_minute": 30
      },
      "run_hook_payload": null,
      "maximum_duration_seconds": 3600,
      "port": 8080,
      "token_expiration_minutes": 30,
      "default_execution_role_arn": "arn:aws:iam::123456789012:role/cognition-agent-runtime",
      "scope": {},
      "source": "api",
      "extra": {}
    }
  ],
  "count": 1
}
```

### `POST /sandbox/profiles`

Create or replace an API-managed sandbox profile.

**Request body:**
```json
{
  "name": "default-lambda",
  "backend": "aws_lambda_microvm",
  "image_arn": "arn:aws:lambda:us-west-2:123456789012:microvm-image:cognition-runtime",
  "image_version": "1.0",
  "region": "us-west-2",
  "egress_mode": "internet",
  "logging": {
    "disabled": {}
  },
  "quota": {
    "max_concurrent_sessions": 10,
    "max_session_starts_per_minute": 30
  },
  "maximum_duration_seconds": 3600,
  "port": 8080,
  "token_expiration_minutes": 30,
  "default_execution_role_arn": "arn:aws:iam::123456789012:role/cognition-agent-runtime",
  "scope": {}
}
```

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | string | Yes | - | Profile selector used by agents |
| `backend` | `"aws_lambda_microvm"` | No | `aws_lambda_microvm` | Sandbox backend type |
| `image_arn` | string | Yes | - | Prebuilt Lambda MicroVM image ARN |
| `image_version` | string | No | `null` | Optional image version |
| `region` | string | No | image ARN region | AWS region |
| `ingress_network_connector_arns` | list[string] | No | `[]` | Optional ingress connector ARNs |
| `egress_mode` | `"internet"` \| `"vpc"` | No | `internet` | Egress policy |
| `egress_network_connector_arns` | list[string] | No | `[]` | Required when `egress_mode` is `vpc` |
| `idle_policy` | object | No | `null` | Lambda MicroVM idle lifecycle policy |
| `logging` | object | No | `null` | Lambda MicroVM logging config; set exactly one of `disabled` or `cloud_watch` |
| `quota` | object | No | `null` | Cognition-side profile/scope quota policy |
| `run_hook_payload` | string | No | `null` | Payload sent to the image `/run` lifecycle hook |
| `maximum_duration_seconds` | int | No | `3600` | Maximum MicroVM runtime, max `28800` |
| `port` | int | No | `8080` | Runtime command server port |
| `token_expiration_minutes` | int | No | `30` | AWS proxy auth token TTL requested by Cognition |
| `default_execution_role_arn` | string | No | `null` | Default IAM execution role for agents using this profile |
| `scope` | dict | No | `{}` | Scope restriction |
| `extra` | dict | No | `{}` | Builder metadata |

**Response `201 Created`:** Sandbox profile object
**Response `409 Conflict`:** Existing profile is file-managed
**Response `422 Unprocessable Entity`:** Validation error

### `GET /sandbox/profiles/{name}`

Get a sandbox profile by name.

**Response `200 OK`:** Sandbox profile object
**Response `404 Not Found`**

### `PATCH /sandbox/profiles/{name}`

Partially update an API-managed sandbox profile.

**Request body (all fields optional):**
```json
{
  "egress_mode": "vpc",
  "egress_network_connector_arns": [
    "arn:aws:lambda:us-west-2:123456789012:network-connector:private-egress"
  ]
}
```

**Response `200 OK`:** Updated sandbox profile object
**Response `404 Not Found`**
**Response `409 Conflict`:** Profile is file-managed
**Response `422 Unprocessable Entity`:** Invalid profile shape

### `DELETE /sandbox/profiles/{name}`

Delete an API-managed sandbox profile.

**Response `204 No Content`**
**Response `404 Not Found`**
**Response `409 Conflict`:** Profile is file-managed

Related: [Lambda MicroVM Sandbox Profiles](../concepts/sandboxes/aws-lambda-microvm/profiles.md).

---

## Artifacts

Artifacts are durable, scope-aware files that agents and builders can read, write, list, and diff. They provide explicit state outside the model context window for long-running agent handoffs.

Artifact types: `scratch` (thread-scoped), `artifact` (user-visible), `contract` (done criteria), `eval` (evaluator results), `memory` (scoped memory), `policy` (read-only org policy).

Visibilities: `private` (session-scoped), `run` (run-scoped), `public` (scope-visible).

### `GET /artifacts`

List all artifacts visible in the current scope.

**Query parameters:**

| Parameter | Type | Description |
|---|---|---|
| `artifact_type` | string | Filter by type (`scratch`, `artifact`, etc.) |
| `run_id` | string | Filter by run ID |

**Response `200 OK`:**
```json
{
  "artifacts": [
    {
      "id": "art-abc123",
      "name": "deployment-plan",
      "artifact_type": "artifact",
      "path": "/plans/deployment.md",
      "content": "# Deployment Plan\n...",
      "content_type": "text/markdown",
      "version": 3,
      "parent_version": 2,
      "run_id": null,
      "checkpoint_id": null,
      "visibility": "public",
      "scope": {"user": "alice"},
      "source": "api",
      "created_at": "2026-05-21T10:00:00Z",
      "updated_at": "2026-05-21T12:00:00Z"
    }
  ],
  "count": 1
}
```

### `POST /artifacts`

Create a new artifact.

**Request body:**
```json
{
  "id": "deployment-plan",
  "name": "Deployment Plan",
  "artifact_type": "artifact",
  "content": "# Deployment Plan\n...",
  "content_type": "text/markdown",
  "visibility": "public",
  "scope": {}
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | Yes | Unique identifier |
| `name` | string | Yes | Human-readable name |
| `artifact_type` | string | Yes | `scratch`, `artifact`, `contract`, `eval`, `memory`, `policy` |
| `content` | string | Yes | Artifact content |
| `content_type` | string | No | MIME type (e.g. `text/markdown`, `application/json`) |
| `path` | string | No | Logical path |
| `run_id` | string | No | Associated run ID |
| `checkpoint_id` | string | No | Associated checkpoint ID |
| `visibility` | string | No | `private`, `run`, or `public` (default: `private`) |
| `scope` | dict | No | Scope restriction |

**Response `201 Created`:** Artifact object  
**Response `422 Unprocessable Entity`:** Invalid type or visibility

### `GET /artifacts/{artifact_id}`

Get the latest version of an artifact.

**Response `200 OK`:** Artifact object  
**Response `404 Not Found`**

### `PUT /artifacts/{artifact_id}`

Update an artifact. Content changes automatically create a new version (version number increments, `parent_version` set to previous version).

**Request body (all fields optional):**
```json
{
  "content": "# Updated plan\n...",
  "name": "Updated Plan",
  "visibility": "public"
}
```

**Response `200 OK`:** Updated artifact object  
**Response `404 Not Found`**  
**Response `409 Conflict`:** Artifact is file-managed

### `DELETE /artifacts/{artifact_id}`

Delete an artifact and all its versions.

**Response `204 No Content`**  
**Response `404 Not Found`**

### `GET /artifacts/{artifact_id}/versions`

List all versions of an artifact, ordered by version descending.

**Response `200 OK`:**
```json
{
  "artifact_id": "deployment-plan",
  "versions": [
    {
      "version": 3,
      "parent_version": 2,
      "content": "# Deployment Plan v3\n...",
      "content_type": "text/markdown",
      "created_at": "2026-05-21T12:00:00Z"
    },
    {
      "version": 2,
      "parent_version": 1,
      "content": "# Deployment Plan v2\n...",
      "content_type": "text/markdown",
      "created_at": "2026-05-21T11:00:00Z"
    }
  ],
  "count": 2
}
```

### `GET /artifacts/{artifact_id}/versions/{version}`

Get a specific version of an artifact.

**Response `200 OK`:** Artifact object at that version  
**Response `404 Not Found`**

---

## Capabilities

### `GET /capabilities`

Returns the deployment's runtime feature set, package versions, and configuration. Use this to discover available features without parsing error messages.

**Response `200 OK`:**
```json
{
  "versions": {
    "cognition": "0.10.0",
    "deepagents": "0.6.3",
    "langgraph": "1.2.0",
    "langchain": "1.3.1",
    "langchain_core": "1.4.0"
  },
  "stream_protocols": ["sse"],
  "sandbox_backends": ["local", "docker", "kubernetes", "aws_lambda_microvm"],
  "features": {
    "async_subagents": true,
    "mcp": true,
    "mcp_tool_name_prefix": true,
    "hitl": true,
    "permissions": true,
    "artifacts": true,
    "context_policy": true,
    "context_controls": true,
    "tool_safety": true,
    "tool_argument_validation": true,
    "trusted_runtime_context": true,
    "checkpoint_apis": true,
    "scope_propagation": true,
    "provider_config_crud": true,
    "agent_config_crud": true,
    "sandbox_profile_crud": true,
    "aws_lambda_microvm_sandbox": true,
    "model_catalog": true,
    "a2a": true,
    "a2a_jsonrpc": true,
    "a2a_streaming": true,
    "a2a_per_agent_cards": true,
    "a2a_push_notifications": false,
    "a2a_grpc": false
  },
  "middleware": [
    "ToolSecurityMiddleware",
    "ToolArgumentValidationMiddleware",
    "TrustedRuntimeContextMiddleware",
    "CognitionObservabilityMiddleware",
    "CognitionStreamingMiddleware",
    "HumanInTheLoopMiddleware",
    "FilesystemMiddleware",
    "MemoryMiddleware",
    "SummarizationToolMiddleware"
  ],
  "scope_keys": ["user"],
  "deployment": {
    "sandbox_backend": "local"
  }
}
```

---

## A2A Protocol

Cognition exposes agents as strict [A2A 1.0](https://a2a-protocol.org/latest/)
JSON-RPC servers. Only agents with `a2a.exposed: true` are visible. Cognition
implements the execution data plane: the embedding application authenticates and
authorizes callers, then supplies trusted `X-Cognition-Scope-*` headers. Cognition
carries that opaque builder-defined scope and isolates agents, tasks, contexts,
messages, events, and artifacts exactly by it; it does not own tenant or IAM models.

The A2A protocol surface can be disabled entirely by setting `COGNITION_A2A_ENABLED=false`. When disabled, the `/.well-known/agent-card.json` and `/a2a/{agent_name}` endpoints are not mounted, and `GET /capabilities` reports `a2a: false`.

`SendMessage` and `SendStreamingMessage` accept all A2A 1.0 Part content
variants. Text and structured data are normalized into ordered model context;
inline raw bytes and URL references become opaque, task-linked artifacts under
the request's exact `effective_scope`. URL Parts are not fetched implicitly.
See [A2A Message Parts](../concepts/a2a-message-parts.md) for persistence,
idempotency, sandbox, and failure semantics.

For endpoints protected by builder-owned ingress, configure public authentication
discovery with `COGNITION_A2A_SECURITY_SCHEMES` and
`COGNITION_A2A_SECURITY_REQUIREMENTS`. Both values use canonical A2A ProtoJSON.
Cognition validates them during startup and publishes them on every generated
card; it does not enforce the advertised authentication scheme.

### `GET /.well-known/agent-card.json`

Return one scope-visible A2A `AgentCard`. Use `?assistant_id={agent_name}` when a
deployment exposes more than one agent. The deterministic first visible agent is
returned when the query parameter is omitted. A specific card is also available at
`GET /a2a/{agent_name}/.well-known/agent-card.json`.

**Headers:**
```
X-Cognition-Scope-User: alice
```

**Response `200 OK`:**
```json
{
  "name": "Deployment Assistant",
  "description": "Handles deployment workflows",
  "supportedInterfaces": [
    {
      "url": "https://agents.example.com/deployment/a2a",
      "protocolBinding": "JSONRPC",
      "protocolVersion": "1.0"
    }
  ],
  "version": "0.12.0-rc.4",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "extendedAgentCard": false
  },
  "securitySchemes": {
    "oauth2": {
      "oauth2SecurityScheme": {
        "description": "Machine credentials",
        "flows": {
          "clientCredentials": {
            "tokenUrl": "https://auth.example.com/oauth/token",
            "scopes": {
              "a2a.invoke": "Invoke the agent"
            }
          }
        },
        "oauth2MetadataUrl": "https://auth.example.com/.well-known/openid-configuration"
      }
    }
  },
  "securityRequirements": [
    {"schemes": {"oauth2": {}}}
  ],
  "defaultInputModes": ["text/plain", "application/json"],
  "defaultOutputModes": ["text/plain", "application/json"],
  "skills": [
    {
      "id": "primary",
      "name": "Deployment Assistant",
      "description": "Handles deployment workflows",
      "tags": ["primary"],
      "inputModes": ["text/plain", "application/json"],
      "outputModes": ["text/plain", "application/json"]
    }
  ]
}
```

When the agent definition includes `a2a.public_interface_url`, Cognition uses
that value exactly for `supportedInterfaces[].url`. Otherwise it derives the
URL from the incoming request and `/a2a/{agent_name}`. `display_name` affects only public presentation; internal lookup
and the fallback route continue to use `name`.

Only agents visible in the exact supplied scope with `a2a.exposed=true` are
returned. Built-in agents are not exposed by default.

Builders configure default MIME modes and public Agent Card skills under the
nested `a2a` object. See the [A2A Builder Guide](a2a.md) for the complete
discovery contract and the distinction between public and runtime skills.

### `POST /a2a/{agent_name}`

Send a JSON-RPC request to a specific agent. The `{agent_name}` is resolved at request time — agents created after the server starts are immediately available without restart.

**Headers:**
```
Content-Type: application/json
A2A-Version: 1.0
X-Cognition-Scope-User: alice
```

**Request body (JSON-RPC 2.0):**
```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "SendMessage",
  "params": {
    "message": {
      "role": "ROLE_USER",
      "messageId": "msg-123",
      "parts": [
        {"text": "Deploy the staging environment", "mediaType": "text/plain"},
        {"data": {"changeTicket": "CHG-42"}, "mediaType": "application/json"},
        {"raw": "cmVsZWFzZTogdjEuMg==", "filename": "release.txt", "mediaType": "text/plain"},
        {"url": "https://example.com/runbook.pdf", "filename": "runbook.pdf", "mediaType": "application/pdf"}
      ]
    }
  }
}
```

Parts are processed in wire order. `data` is rendered as a delimited JSON block.
`raw` and `url` become artifact references in the normalized user message; their
payload or remote content is not inserted into the prompt. Part metadata cannot
override trusted request scope. A Part with no content variant is rejected before
the model run starts.

**Supported methods:**

| Method | Description |
|---|---|
| `SendMessage` | Send a message and get the complete response |
| `SendStreamingMessage` | Send a message and stream the response (SSE) |
| `GetTask` | Read one task by `id` |
| `ListTasks` | List exact-agent, exact-scope tasks with cursor pagination |
| `CancelTask` | Atomically cancel one non-terminal task |
| `SubscribeToTask` | Replay and follow one non-terminal task over SSE |

**Response `200 OK` (SendMessage):**
```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "result": {
    "task": {
      "id": "task-abc123",
      "contextId": "context-456",
      "status": {
        "state": "TASK_STATE_COMPLETED"
      },
      "history": [],
      "artifacts": []
    }
  }
}
```

`SendMessage` returns either `result.task` for task-oriented work or
`result.message` for a direct message response. `SendStreamingMessage` and
`SubscribeToTask` use `Content-Type: text/event-stream`; every SSE `data` value is
a complete JSON-RPC 2.0 response envelope containing `task`, `message`,
`statusUpdate`, or `artifactUpdate`.

Tasks are durable and independent from execution attempts. A continuation after
`TASK_STATE_INPUT_REQUIRED` keeps the same A2A task and context IDs while creating
a new Cognition run. Get, list, continuation, subscription, and cancellation
remain available after a process restart or on another replica when the deployment
uses shared durable storage.

Send operations may use `messageId` as an idempotency identity. Cognition
namespaces it by selected agent and exact effective scope so a retry does not
create a second task or run and cannot collide across application scopes.

**Errors:**

- HTTP `404` — agent not found, not visible in scope, or not A2A-exposed.
- A2A protocol failures use HTTP `200` with a structured JSON-RPC `error`,
  including `TaskNotFoundError`, `TaskNotCancelableError`,
  `UnsupportedOperationError`, `ContentTypeNotSupportedError`, and
  `VersionNotSupportedError`.
- A task owned by another agent or scope is reported as not found.
- `SubscribeToTask` and a new `SendMessage` continuation reject terminal tasks.

Push notifications, gRPC, HTTP+JSON, and authenticated extended cards are not
advertised. The JSON-RPC 1.0 MUST profile is checked with the official
[A2A TCK](https://github.com/a2aproject/a2a-tck).

---

## Builder-Defined Runtime Scoping

When `COGNITION_SCOPING_ENABLED=true`, all session endpoints require scope headers. The required headers are determined by `COGNITION_SCOPE_KEYS` — these are **builder-defined** key names. Cognition does not hardcode a vocabulary.

For `scope_keys: ["user", "project"]`:

```
X-Cognition-Scope-User: alice
X-Cognition-Scope-Project: proj-123
```

For `scope_keys: ["tenant", "env"]`:

```
X-Cognition-Scope-Tenant: acme
X-Cognition-Scope-Env: production
```

Missing required headers return `403 Forbidden`:

```json
{
  "error": "Missing required scope header: x-cognition-scope-user",
  "code": "PERMISSION_DENIED"
}
```

Sessions are automatically filtered to match the request's exact scope values. A
resource in one authorized scope cannot be read or mutated from another scope. The
`effective_scope` dict propagates through the full runtime stack — ConfigRegistry
CRUD, session persistence, `CognitionContext`, middleware, and tools. This lets
Cognition serve as the isolated runtime inside a multi-tenant host application;
Cognition itself does not define tenants, memberships, roles, or entitlements.

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
