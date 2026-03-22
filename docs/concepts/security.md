# Security

Cognition is designed to run untrusted agent workloads in multi-tenant environments. Security controls are applied at every layer — network, process, filesystem, and API. This document describes each control, where it is implemented, and how to configure it.

---

## Multi-Tenant Session Scoping

Implemented in `server/app/api/scoping.py`.

Session scoping is Cognition's multi-tenancy mechanism. When enabled, every session carries a set of key-value pairs (its scope), and API requests must supply matching headers. Scopes prevent one tenant's sessions from being visible or accessible to another tenant.

### How It Works

1. `COGNITION_SCOPING_ENABLED=true` activates scope enforcement.
2. `COGNITION_SCOPE_KEYS` defines which dimensions are required (default: `["user"]`).
3. For each key in `scope_keys`, the request must include an `x-cognition-scope-{key}` header.
4. Missing headers return `403 Forbidden` immediately — **fail-closed**.
5. When listing sessions, results are filtered to only sessions whose scope values match the request headers.

### Configuration

```env
COGNITION_SCOPING_ENABLED=true
COGNITION_SCOPE_KEYS=["user", "project"]
```

### Usage

```bash
# Create a session scoped to user + project
curl -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -H "X-Cognition-Scope-User: alice" \
  -H "X-Cognition-Scope-Project: proj-123" \
  -d '{"title": "My session"}'

# List sessions — only returns sessions for this user+project
curl http://localhost:8000/sessions \
  -H "X-Cognition-Scope-User: alice" \
  -H "X-Cognition-Scope-Project: proj-123"
```

A request without the required scope headers returns:
```json
{"error": "Missing required scope header: x-cognition-scope-user", "code": "PERMISSION_DENIED"}
```

### Scope Matching Logic

`SessionScope.matches(other_scopes)` checks that every key in the session's scope has a matching value in the request's scope. Sessions with no scopes are only visible when scoping is disabled.

---

## Sandbox Isolation

Implemented in `server/app/agent/sandbox_backend.py` and `server/app/execution/backend.py`.

### No `shell=True`

Every command executed by the agent goes through `shlex.split()` followed by `subprocess.run()` with `shell=False`. This eliminates shell injection vulnerabilities — a command like `; rm -rf /` cannot be executed because the shell metacharacters are passed as literal argument strings.

### Protected Paths

`CognitionLocalSandboxBackend` maintains a list of protected paths that cannot be modified by the agent. By default, `.cognition/` is protected. Any write operation (file write, file delete, directory creation inside a protected path) is blocked before execution.

```env
COGNITION_PROTECTED_PATHS=[".cognition/", ".git/"]
```

### Docker Isolation

When `COGNITION_SANDBOX_BACKEND=docker`, code runs in a Docker container with all available Linux security controls applied:

| Control | Setting | Effect |
|---|---|---|
| Capabilities | `cap_drop: ALL` | All Linux capabilities removed |
| Privilege escalation | `no-new-privileges: true` | Processes cannot gain privileges |
| Root filesystem | `read_only: true` | Cannot write outside allowed paths |
| Writable mounts | `tmpfs:/tmp`, `tmpfs:/home` | Only temp directories are writable |
| Network | `network_mode: none` (default) | No outbound or inbound network access |
| Memory | `512m` (default) | Hard memory ceiling |
| CPU | `1.0` (default) | CPU quota |

The container is created from `cognition-sandbox:latest`, a minimal image without unnecessary tools. See [Deployment](../guides/deployment.md) for building the sandbox image.

---

## Tool Security

Implemented in `server/app/agent/middleware.py:ToolSecurityMiddleware`.

### Trust Model

Tool source code (both file-discovered and API-registered) executes with full Python privileges inside the sandbox backend. Cognition does not perform AST scanning or Python-level restrictions on tool code — these were removed as they were bypassable via reflection and created a false sense of security.

**The real security boundaries are:**

| Boundary | Mechanism |
|---|---|
| API authorization | Gateway/proxy layer — Cognition assumes authenticated callers |
| Per-name tool blocking | `ToolSecurityMiddleware` — `COGNITION_BLOCKED_TOOLS` blocklist enforced at call time |
| Process isolation | Docker sandbox backend — container per session |
| Network isolation | Docker `network_mode=none` |
| Filesystem isolation | `CognitionLocalSandboxBackend` protected paths |
| Memory isolation | LangGraph Store namespaces scoped per user via `CognitionContext` |

`POST /tools` (API-registered tools) executes arbitrary Python with full privileges. **Restrict this endpoint to authorized administrators at the Gateway/proxy layer.**

For a detailed explanation, see [AGENTS.md — Tool Security Trust Model](../../AGENTS.md).

### Tool Namespace Allowlist

Tool imports are validated against a set of trusted namespaces before the agent starts. This prevents agent definitions from loading arbitrary Python code by specifying a malicious import path.

```env
COGNITION_TRUSTED_TOOL_NAMESPACES=["myapp.tools", "cognition_tools"]
```

If a tool's dotted path does not start with a trusted namespace, it is rejected at agent creation time. An empty `trusted_tool_namespaces` list disables the check (all namespaces allowed — suitable only for development).

### Tool Blocklist

`ToolSecurityMiddleware` intercepts every tool call before execution. If the tool name is in the blocked list, the call returns an error `ToolMessage` without executing the tool.

```env
COGNITION_BLOCKED_TOOLS=["file_write", "execute_bash"]
```

The blocked call returns:
```
Tool 'file_write' is blocked by the tool security policy.
```

---

## MCP Remote-Only Policy

Implemented in `server/app/agent/mcp_client.py:McpServerConfig`.

MCP (Model Context Protocol) tool servers must be remote HTTP/HTTPS servers. Stdio-based MCP servers (which would spawn a local subprocess) are not supported:

```python
@field_validator("url")
def validate_url_is_remote(cls, v: str) -> str:
    if not v.startswith(("http://", "https://")):
        raise ValueError("MCP server URL must be HTTP or HTTPS (no stdio)")
    return v
```

This policy ensures MCP tool servers cannot be used to execute arbitrary local processes.

---

## Rate Limiting

Implemented in `server/app/rate_limiter.py`.

The rate limiter uses a **token bucket** algorithm with one bucket per scope key (or per IP address when scoping is disabled). Buckets refill continuously at the configured rate.

| Setting | Variable | Default |
|---|---|---|
| Requests per minute | `COGNITION_RATE_LIMIT_PER_MINUTE` | `60` |
| Burst allowance | `COGNITION_RATE_LIMIT_BURST` | `10` |

The `burst` parameter allows short-lived traffic spikes above the per-minute rate. Once the burst allowance is exhausted, requests are throttled until the bucket refills.

Exceeded limits return:
```http
HTTP/1.1 429 Too Many Requests
Retry-After: 4

{"error": "Rate limit exceeded", "code": "RATE_LIMITED"}
```

Buckets are keyed on the scope value (e.g. `user:alice`) when scoping is enabled, or on the client IP address otherwise. Inactive buckets are cleaned up every 5 minutes.

---

## CORS

Implemented in `server/app/main.py` via FastAPI's `CORSMiddleware`.

All CORS settings are configurable without code changes:

```env
COGNITION_CORS_ORIGINS=["https://app.example.com", "https://admin.example.com"]
COGNITION_CORS_METHODS=["GET", "POST", "PATCH", "DELETE"]
COGNITION_CORS_HEADERS=["Content-Type", "Authorization", "X-Cognition-Scope-User"]
COGNITION_CORS_ALLOW_CREDENTIALS=true
```

In development, `COGNITION_CORS_ORIGINS=["*"]` is acceptable. In production, restrict to known origins.

---

## Security Headers

`server/app/api/middleware.py:SecurityHeadersMiddleware` adds the following headers to every response:

| Header | Value |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `X-XSS-Protection` | `1; mode=block` |

These prevent MIME sniffing, clickjacking, and reflected XSS attacks in browser contexts.

---

## Secrets Management

- API keys and credentials must be set via environment variables or `.env` files — never in YAML config files committed to version control.
- The `GET /config` endpoint returns sanitized configuration: `SecretStr` fields are masked as `**redacted**`.
- The `.env` file is listed in `.gitignore` by default.
- The `COGNITION_OPENAI_API_KEY` and similar secret settings use Pydantic's `SecretStr` type so they never appear in logs or error messages.

---

## Production Security Checklist

- [ ] Set `COGNITION_SCOPING_ENABLED=true` and configure `COGNITION_SCOPE_KEYS`
- [ ] Set `COGNITION_SANDBOX_BACKEND=docker`
- [ ] Set `COGNITION_DOCKER_NETWORK=none`
- [ ] Restrict `POST /tools` to authorized administrators at the Gateway/proxy layer
- [ ] Set `COGNITION_TRUSTED_TOOL_NAMESPACES` to your allowed namespaces
- [ ] Set `COGNITION_CORS_ORIGINS` to your specific frontend domains
- [ ] Set `COGNITION_RATE_LIMIT_PER_MINUTE` appropriate for your load
- [ ] Never commit API keys; use `.env` or secrets management (Vault, AWS Secrets Manager)
- [ ] Run the sandbox image from a minimal, audited base image
- [ ] Set `COGNITION_PROTECTED_PATHS` to include any sensitive directories
