# Security

Cognition is designed to run untrusted agent workloads, including as the execution
runtime inside a multi-tenant host application. Security controls are applied at
every layer — network, process, filesystem, and API. The host application owns
authentication, tenancy, membership, roles, and entitlements; Cognition enforces
the authorized runtime scope it receives.

---

## Builder-Defined Runtime Scoping

Implemented in `server/app/api/scoping.py`.

Session scoping is Cognition's runtime isolation mechanism. When enabled, every
session carries opaque builder-authorized key-value pairs (`effective_scope`), and
API requests must supply matching trusted headers. Exact scope enforcement prevents
one application scope from observing or mutating another. It does not create a
tenant domain model inside Cognition.

Scope keys are **builder-defined** — Cognition does not hardcode a vocabulary. Choose keys that match your application's tenancy model (e.g. `user`, `tenant`, `project`, `env`).

### How It Works

1. `COGNITION_SCOPING_ENABLED=true` activates scope enforcement.
2. `COGNITION_SCOPE_KEYS` defines which key names are required (default: `["user"]`).
3. For each key in `scope_keys`, the request must include an `x-cognition-scope-{key}` header.
4. Missing headers return `403 Forbidden` immediately — **fail-closed**.
5. When listing sessions, results are filtered to only sessions whose scope values match the request headers.
6. The resulting `effective_scope` dict propagates through `CognitionContext` → LangGraph `runtime.context` → middleware → tools.

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

### AWS Lambda MicroVM Isolation

When `COGNITION_SANDBOX_BACKEND=aws_lambda_microvm`, command execution runs in
an AWS Lambda MicroVM launched from a trusted `SandboxProfile`.

Key security properties:

- MicroVM image ARN and network connectors come from builder-managed profile
  config, not model tool arguments.
- Per-agent `sandbox_execution_role_arn` is resolved from trusted agent config,
  with profile default role fallback.
- Cognition requests AWS proxy auth tokens at runtime and keeps them in memory
  only.
- Streamed and persisted sandbox metadata contains a role fingerprint, not
  runtime credentials or auth tokens.
- The Cognition control-plane IAM identity should be limited to approved
  MicroVM images, network connectors, and execution roles.

See [AWS Lambda MicroVM Sandbox](./sandboxes/aws-lambda-microvm/index.md) for
the full backend model.

---

## Tool Security

Implemented in `server/app/agent/middleware.py:ToolVisibilityMiddleware` and `ToolSecurityMiddleware`.

### Trust Model

Tool source code (both file-discovered and API-registered) executes with full Python privileges inside the sandbox backend. Cognition does not perform AST scanning or Python-level restrictions on tool code — these were removed as they were bypassable via reflection and created a false sense of security.

**The real security boundaries are:**

| Boundary | Mechanism |
|---|---|
| API authorization | Gateway/proxy layer — Cognition assumes authenticated callers |
| Per-agent tool visibility | `ToolVisibilityMiddleware` — `config.excluded_tools` removes matching tools from the model-visible schema |
| Per-name tool blocking | `ToolSecurityMiddleware` — `config.blocked_tools` plus `COGNITION_BLOCKED_TOOLS` are enforced at call time |
| Process isolation | Docker, Kubernetes, or AWS Lambda MicroVM sandbox backend |
| Network isolation | Docker `network_mode=none`, Kubernetes network policy, or Lambda MicroVM connector policy |
| Filesystem isolation | `CognitionLocalSandboxBackend` protected paths |
| Memory isolation | LangGraph Store namespaces derived from exact `CognitionContext.effective_scope` |

`POST /tools` (API-registered tools) executes arbitrary Python with full privileges. **Restrict this endpoint to authorized administrators at the Gateway/proxy layer.**

For a detailed explanation, see the Tool Security Trust Model section in `AGENTS.md`.

### Tool Namespace Allowlist

Tool imports are validated against a set of trusted namespaces before the agent starts. This prevents agent definitions from loading arbitrary Python code by specifying a malicious import path.

```env
COGNITION_TRUSTED_TOOL_NAMESPACES=["myapp.tools", "cognition_tools"]
```

If a tool's dotted path does not start with a trusted namespace, it is rejected at agent creation time. An empty `trusted_tool_namespaces` list disables the check (all namespaces allowed — suitable only for development).

### Tool Visibility and Blocklists

Per-agent `excluded_tools` and `blocked_tools` serve different purposes:

| Policy | Effect |
|---|---|
| `excluded_tools` | Hides matching tools from the model before the model can choose them. Use this to remove inherited Deep Agents harness tools from customer-facing or constrained agents. |
| `blocked_tools` | Allows the tool to remain visible but denies execution if the model calls it. Use this as a call-time guardrail and audit point. |

If a tool should be both invisible and guarded, include the same tool name in both lists.

`ToolSecurityMiddleware` intercepts every tool call before execution. If the tool name is in the blocked list, the call returns an error `ToolMessage` without executing the tool.

```env
COGNITION_BLOCKED_TOOLS=["file_write", "execute_bash"]
```

`COGNITION_BLOCKED_TOOLS` is deployment-wide and merges with each agent's `config.blocked_tools`. It does not hide tools from the model-visible schema. Use per-agent `config.excluded_tools` for that.

Agent definitions can set both lists:

```yaml
config:
  excluded_tools:
    - grep
    - ls
    - websearch
  blocked_tools:
    - execute
```

The blocked call returns:
```
Tool 'file_write' is disabled by server policy.
```

---

## MCP Remote-Only Policy

Implemented in `server/app/agent/mcp_client.py:McpServerConfig`.

MCP (Model Context Protocol) tool servers must be remote HTTP/HTTPS servers. Stdio-based MCP servers (which would spawn a local subprocess) are not supported:

```python
@field_validator("url")
def validate_url(cls, v: str) -> str:
    if not v.startswith(("http://", "https://")):
        raise ValueError("MCP server URL must be HTTP or HTTPS (no stdio)")
    return v
```

This policy ensures MCP tool servers cannot be used to execute arbitrary local processes.

Additional MCP security measures:
- **Header redaction** — `GET /mcp-servers` returns empty `headers` dicts to prevent credential leakage
- **File-managed immutability** — servers from `.cognition/config.yaml` cannot be modified via API (409 on mutation)
- **Scope injection** — `X-Cognition-Scope-*` headers are automatically added to MCP requests via `ToolCallInterceptor`
- **Tool name prefixing** — `tool_name_prefix=True` on `MultiServerMCPClient` prevents tool name collisions

---

## A2A Protocol Boundary

The A2A (Agent-to-Agent) protocol adapter is a Cognition protocol surface, not an app-layer concern.
See [A2A Security and Scoping](a2a/security-and-scoping.md) for the focused
protocol-boundary model.

**Security boundary**: Trusted ingress supplies configured `X-Cognition-Scope-*`
headers. Per-agent card discovery, root discovery, and JSON-RPC dispatch are filtered
by exact builder-defined scope. Tasks, contexts, runs, messages, events, artifacts,
continuation, subscriptions, and cancellation retain that immutable scope. A
cross-scope or cross-agent identifier is reported as not found.

**Builder responsibility**: Cognition does not perform end-user authentication or
own tenant, organization, membership, role, billing, entitlement, or route-selection
models. Authorization must be completed by the embedding application or trusted
gateway before requests reach Cognition. `a2a.exposed` controls which definitions
are visible; set it only on agents intended for A2A access.

**Global disable**: Set `COGNITION_A2A_ENABLED=false` to prevent the A2A protocol surface from being mounted at all. When disabled, the endpoints do not exist and `GET /capabilities` reports `a2a: false`.

**Authentication discovery**: When trusted ingress protects the advertised A2A
interface, set `COGNITION_A2A_SECURITY_SCHEMES` and
`COGNITION_A2A_SECURITY_REQUIREMENTS` to canonical A2A ProtoJSON. Cognition
publishes the validated values in its Agent Cards but does not enforce them.
Gateway enforcement must match the card. Because Agent Cards are public, never
place client secrets, bearer tokens, or private credentials in these values.

**Message Parts**: Every inbound A2A Part inherits the trusted request's exact
`effective_scope`; Part metadata, filenames, and URLs cannot set or alter scope.
Raw bytes and URL references are persisted as task-linked artifacts under that
scope. Receiving a Part never executes a file or fetches a URL. Interpretation,
retrieval, and transformation require an explicit builder-authorized tool or
sandbox operation. See [A2A Message Parts](a2a/message-parts.md).

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

- [ ] Set `COGNITION_SCOPING_ENABLED=true` and configure `COGNITION_SCOPE_KEYS` (builder-defined keys matching your tenancy model)
- [ ] Set `COGNITION_SANDBOX_BACKEND=docker`
- [ ] Set `COGNITION_DOCKER_NETWORK=none`
- [ ] Restrict `POST /tools` to authorized administrators at the Gateway/proxy layer
- [ ] Set `COGNITION_TRUSTED_TOOL_NAMESPACES` to your allowed namespaces
- [ ] Use per-agent `excluded_tools` to hide inherited harness tools from agents that should not see them
- [ ] Use per-agent or global `blocked_tools` for tools that must be denied even if a call is attempted
- [ ] Set `COGNITION_CORS_ORIGINS` to your specific frontend domains
- [ ] Set `COGNITION_RATE_LIMIT_PER_MINUTE` appropriate for your load
- [ ] Never commit API keys; use `.env` or secrets management (Vault, AWS Secrets Manager)
- [ ] Run the sandbox image from a minimal, audited base image
- [ ] Set `COGNITION_PROTECTED_PATHS` to include any sensitive directories
- [ ] Review which agents have `a2a.exposed: true` — only expose agents intended for external A2A access
- [ ] Restrict `/mcp-servers` CRUD to authorized administrators (MCP server headers contain credentials)
