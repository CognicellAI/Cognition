# Extending Agents

Cognition uses a definition-driven extension model. Agent behavior is assembled from Agent definitions, skills, MCP servers, middleware, and sandbox backends; v0.14 no longer loads Cognition-managed Python tool files or `/tools` API registrations.

| Level | Mechanism | Code Required | Hot-Reload |
|---|---|---|---|
| Memory | `AGENTS.md` | No | Yes |
| Skills | Complete bundles in an Agent definition | No | Yes, with the Agent revision |
| Agents | `.cognition/agents/` YAML or Markdown | No | Yes |
| Middleware | Python classes | Yes | No |
| MCP servers | Agent-owned remote Streamable HTTP endpoints | No | Yes |
| A2A exposure | `a2a.exposed: true` on agent definition | No | Yes |
| Custom LLM providers | Python factories | Yes | No |

---

## 1. Memory (AGENTS.md)

Place an `AGENTS.md` file in your project root. It is automatically injected into the agent's system prompt for every session in that project.

Use memory for:
- Project-specific rules and conventions
- Architecture decisions
- Code style guidelines
- Workflow instructions

```markdown
# My Project

This is a Django REST API. All models live in `myapp/models/`.
Use Python 3.11 type hints everywhere. Tests run with pytest.
The database is PostgreSQL — never use SQLite in tests.

## Conventions
- Prefer `select_related` over multiple queries
- All API views inherit from `BaseAPIView`
- Migrations must be reviewed before merging
```

Configure which memory files to load in `.cognition/config.yaml`:

```yaml
agent:
  memory:
    - "AGENTS.md"
    - "docs/architecture.md"    # additional files
```

---

## 2. Skills (SKILL.md)

Skills are modular instruction sets for domain-specific tasks. The agent sees a skill's name and description and loads the full content only when it is relevant to the current task (progressive disclosure).

### SKILL.md Format

```markdown
# Deploy App

Use this skill when the user asks to deploy the application or push changes to production.

## Prerequisites
- Docker must be running
- AWS credentials must be configured

## Steps
1. Run the test suite: `uv run pytest`
2. Build the Docker image: `docker build -t myapp:latest .`
3. Push to ECR: `docker push <account>.dkr.ecr.us-east-1.amazonaws.com/myapp:latest`
4. Update the ECS service: `aws ecs update-service --cluster prod --service myapp --force-new-deployment`
```

Skills are complete bundles inside the Agent definition. `content` is the
bundle's `SKILL.md`; `files` carries progressive-disclosure supporting files:

```yaml
agent:
  skills:
    - name: my-skill
      content: |
        ---
        name: my-skill
        description: Apply the project review workflow
        ---

        # Review workflow

        Read `references/checklist.md` before reviewing.
      files:
        references/checklist.md: |
          - Verify tests
          - Check tenant isolation
```

---

## 3. Custom Agents

Place agent definitions in `.cognition/agents/` as Markdown or YAML files. The file watcher reloads them automatically on change.

### Markdown Format

The filename (without extension) becomes the agent name. The YAML frontmatter provides fields; the Markdown body becomes the system prompt.

```markdown
---
# .cognition/agents/security-auditor.md
mode: subagent
description: Audits code for security vulnerabilities and reports findings with severity ratings
tools:
  - "run_semgrep"
config:
  model: gpt-4o
  temperature: 0.1
  excluded_tools:
    - grep
    - ls
    - websearch
  blocked_tools:
    - execute
---

You are a security expert specialising in Python web applications.

When asked to audit code:
1. Check for SQL injection, XSS, CSRF, and path traversal vulnerabilities
2. Review dependency versions for known CVEs
3. Report findings with severity (Critical/High/Medium/Low) and remediation steps
```

### YAML Format

```yaml
# .cognition/agents/data-analyst.yaml
name: data-analyst
mode: primary
description: Analyses datasets and generates statistical reports
system_prompt: |
  You are a data analyst. Use pandas and matplotlib for analysis.
  Always validate data quality before drawing conclusions.
tools:
  - "load_csv"
  - "plot_chart"
config:
  model: gpt-4o
  temperature: 0.2
  excluded_tools:
    - websearch
  blocked_tools:
    - execute
```

### Agent Modes

| Mode | Can own a session | Can be delegated to |
|---|---|---|
| `primary` | Yes | No |
| `subagent` | No | Yes |
| `all` | Yes | Yes |

Sessions are created with `agent_name`:
```bash
curl -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"agent_name": "data-analyst"}'
```

Primary agents can delegate to subagents via the `task` tool. The delegation appears as a `delegation` SSE event.

### Per-Agent Tool Policy

Agent definitions can hide or block runtime tools inherited from Deep Agents and Cognition middleware.

```yaml
config:
  excluded_tools:
    - glob
    - grep
    - ls
    - websearch
  blocked_tools:
    - execute
```

`excluded_tools` removes matching tool names from the model-visible tool list before the model can select them. Use it for agent profiles that should not see general-purpose harness tools, such as customer-facing tenant assistants.

`blocked_tools` denies matching tool calls at execution time through `ToolSecurityMiddleware`. It is separate from exclusion so builders can keep a tool visible but guarded, or place the same name in both lists for no model affordance plus a runtime guard.

File-based agent definitions put these fields under `config:`. The `/agents` API accepts them as top-level `excluded_tools` and `blocked_tools` fields and returns them under `config.excluded_tools` and `config.blocked_tools`.

---

## 4. Tool Capability

Cognition v0.14 does not load Cognition-managed Python tools from `.cognition/tools/`, `/tools`, inline source code, or module paths. Use one of these supported surfaces instead:

| Need | Supported surface |
|---|---|
| Remote provider or builder-managed tools | Agent-owned MCP servers |
| Procedural guidance, scripts, and reusable instructions | Skills installed into the Deep Agents backend/sandbox |
| Runtime policy, telemetry, or request shaping | Deep Agents/LangChain middleware |
| Filesystem/process execution | Configured sandbox backend and Deep Agents-native filesystem/runtime tools |

For external tools, prefer MCP. See [MCP Tool Servers](#6-mcp-tool-servers).

---

## 5. Middleware

Middleware intercepts the agent's processing loop. Use middleware for cross-cutting concerns: approval gates, custom telemetry, PII detection, retry logic.

### Upstream Middleware (No Code)

Four upstream middleware components are available by name in `agent.middleware`:

```yaml
agent:
  middleware:
    # Retry failed tool calls with exponential backoff
    - name: tool_retry
      max_retries: 3
      backoff_factor: 2.0

    # Hard cap on total tool invocations
    - name: tool_call_limit
      run_limit: 50
      per_tool_limits:
        execute_bash: 10

    # Detect and redact PII before sending to the LLM
    - name: pii
      pii_types:
        - email
        - phone
        - credit_card
        - ip
        - ssn
      strategy: redact   # or "mask"

    # Require human approval before specific tools execute
    - name: human_in_the_loop
      approve_tools:
        - execute_bash
        - file_write
```

### Custom Middleware

Implement `deepagents.middleware.AgentMiddleware` and register it in `.cognition/config.yaml` as a dotted import path.

```python
# myapp/middleware/audit.py
from deepagents.middleware import AgentMiddleware
from myapp.audit_log import write_audit_event

class AuditMiddleware(AgentMiddleware):
    """Writes every tool call to an immutable audit log."""

    async def awrap_tool_call(self, tool_call, handler):
        # Called before the tool executes
        write_audit_event(
            event_type="tool_call",
            tool=tool_call.name,
            args=tool_call.args,
        )
        result = await handler(tool_call)
        # Called after the tool executes
        write_audit_event(
            event_type="tool_result",
            tool=tool_call.name,
            exit_code=result.exit_code,
        )
        return result
```

Register in config:

```yaml
agent:
  middleware:
    - "myapp.middleware.audit.AuditMiddleware"
    - name: tool_retry
      max_retries: 2
```

String entries are imported directly; dict entries with a `name` key are treated as upstream middleware.

---

## 6. MCP Tool Servers

Connect an Agent to remote Model Context Protocol (MCP) servers. Servers are
declared on the Agent and are part of its immutable configuration revision.

```yaml
name: deploy-agent
mcp:
  servers:
    github:
      url: https://mcp.github.example.com/mcp
      transport: streamable_http
      required: true
      auth:
        type: mcp_oauth
    internal-db:
      url: https://db-tools.internal/mcp
      required: false
      auth:
        type: workload_token_exchange
        profile: internal-egress
    legacy-service:
      url: https://legacy-tools.internal/mcp
      required: false
      auth:
        type: static_bearer
        env: LEGACY_MCP_TOKEN
```

Each server is discovered independently. A required-server failure stops the
run with a typed, redacted error; an optional-server failure leaves healthy
servers available. Duplicate canonical tool identities
`(server_alias, provider_tool_name)` fail discovery.

### How It Works

Cognition uses [`langchain-mcp-adapters`](https://github.com/langchain-ai/langchain-mcp-adapters) to connect to MCP servers. The adapter:

1. Connects to each declared remote server using Streamable HTTP transport
2. Converts MCP tools into LangChain `BaseTool` instances
3. Applies transport authentication during discovery and invocation
4. Returns tools that participate in the full Deep Agents middleware stack (tool safety, HITL, permissions)

The supported authentication types are `none`, standard `mcp_oauth`, built-in
`workload_token_exchange`, and environment-backed `static_bearer`. Workload
exchange references an opaque deployment profile and authenticates the
Cognition workload to a builder-controlled endpoint; that endpoint performs
live Agent authorization and may inject its upstream provider credential.
`static_bearer` is supported but not recommended. Cognition does not classify
deployments or ban a mode on the builder's behalf.

Raw headers, tokens, API keys, provider credentials, and Python authentication
callbacks are invalid Agent configuration. Only workload token exchange sends a
fixed trusted-runtime envelope; it is constructed from the pinned Agent and run
context and cannot be altered by the model.

Only HTTP/HTTPS URLs are accepted — stdio-based MCP servers and raw configured
headers are deliberately unsupported. The complete v0.14 contract is in
[the MCP runtime proposal](../proposals/v0.14.0-mcp-runtime-contract.md).

---

## 7. Exposing Agents via A2A

Cognition can expose agents as strict [A2A 1.0](https://a2a-protocol.org/latest/)
JSON-RPC servers, allowing external systems to discover and invoke them.

### Opting In

Set `a2a.exposed: true` on any agent definition:

```yaml
# .cognition/agents/deploy-agent.yaml
name: deploy-agent
mode: primary
a2a:
  exposed: true
description: Handles deployment workflows
system_prompt: |
  You are a deployment agent. Deploy applications safely and report results.
```

Or via the API:

```bash
curl -X POST http://localhost:8000/agents \
  -H "Content-Type: application/json" \
  -d '{"name": "deploy-agent", "system_prompt": "...", "a2a": {"exposed": true}}'
```

### How It Works

1. **Agent card discovery** — `GET /a2a/{agent_name}/.well-known/agent-card.json` returns the A2A `AgentCard` for that agent. `GET /.well-known/agent-card.json?assistant_id={agent_name}` returns the same single-card shape from the standard well-known path.
2. **JSON-RPC endpoint** — Each agent gets a dedicated endpoint at `POST /a2a/{agent_name}`. The agent is resolved at request time, so agents created after server startup are immediately available.
3. **Scope-aware runtime isolation** — Trusted ingress supplies builder-defined `X-Cognition-Scope-*` headers. Cognition carries the exact scope through tasks, contexts, runs, messages, events, and artifacts without becoming the application's tenant or IAM system.
4. **Shared durable lifecycle** — Native REST/SSE and A2A use the same task/session/run service. A2A tasks survive restarts, continuation creates a new run under the same task, and subscriptions replay durable events.
5. **Typed message Parts** — Text and structured data enter model context. Inline bytes and URL references become scoped, task-linked artifacts; receiving a URL never performs an implicit network request. Mixed-Part order is preserved. See [A2A Message Parts](../concepts/a2a/message-parts.md).

### A2A Client Example

```python
import httpx

# Discover a specific agent card from the agent's canonical A2A URL
resp = httpx.get(
    "http://localhost:8000/a2a/deploy-agent/.well-known/agent-card.json",
    headers={"X-Cognition-Scope-User": "alice"},
)
card = resp.json()

# Send a message to an agent
resp = httpx.post(
    "http://localhost:8000/a2a/deploy-agent",
    json={
        "jsonrpc": "2.0",
        "id": "1",
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "deploy-staging-1",
                "role": "ROLE_USER",
                "parts": [{"text": "Deploy staging", "mediaType": "text/plain"}],
            }
        },
    },
    headers={
        "A2A-Version": "1.0",
        "X-Cognition-Scope-User": "alice",
    },
)
```

Messages may combine all A2A 1.0 Part content variants:

```json
{
  "role": "ROLE_USER",
  "messageId": "analyze-input-1",
  "parts": [
    {"text": "Analyze this payload", "mediaType": "text/plain"},
    {"data": {"priority": 3}, "mediaType": "application/json"},
    {"raw": "aGVsbG8=", "filename": "note.txt", "mediaType": "text/plain"},
    {"url": "https://example.com/report.pdf", "filename": "report.pdf", "mediaType": "application/pdf"}
  ]
}
```

The URL is retained as a scoped reference; Cognition does not download it during
request handling. File parsing and remote retrieval require explicit tools and
the deployment's sandbox/network policy.

### Constraints

- Built-in agents (`default`, `readonly`) have `a2a.exposed=false` by default
- Only `primary` and `all` mode agents can be exposed via A2A
- If `A2A-Version` is omitted, Cognition treats the request as the current supported A2A version
- Dynamically registered agents are scope-bound. Use the same `X-Cognition-Scope-*` headers when creating, discovering, and invoking an agent.
- JSON-RPC is the only advertised binding. Push notifications, gRPC, HTTP+JSON, and extended cards are disabled.
- The supported methods are `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`, and `SubscribeToTask`.
- A2A does not add any additional services — endpoints are part of the main Cognition server

For the complete Cognition builder contract—including public skills, MIME modes,
Agent Cards, message Parts, authentication discovery, and scope isolation—see
the [A2A Builder Guide](a2a.md). For protocol details, see the
[A2A SDK documentation](https://github.com/a2aproject/a2a-python).

---

## 8. Custom LLM Providers

Cognition uses LangChain's `init_chat_model()` under the hood, which supports any provider that has a LangChain integration. The built-in provider types are:

| Type | LangChain Package | Credentials |
|---|---|---|
| `openai` | `langchain-openai` | `OPENAI_API_KEY` |
| `anthropic` | `langchain-anthropic` | `ANTHROPIC_API_KEY` |
| `bedrock` | `langchain-aws` | AWS IAM credentials |
| `google_genai` | `langchain-google-genai` | `GOOGLE_API_KEY` |
| `google_vertexai` | `langchain-google-vertexai` | Google ADC |
| `openai_compatible` | `langchain-openai` + custom `base_url` | `COGNITION_OPENAI_COMPATIBLE_API_KEY` |

To add a provider, create a `ProviderConfig` entry via the REST API:

```bash
curl -X POST http://localhost:8000/models/providers \
  -H "Content-Type: application/json" \
  -d '{
    "id": "my-provider",
    "provider": "openai_compatible",
    "model": "my-model",
    "base_url": "https://my-provider.example.com/v1",
    "api_key_env": "MY_PROVIDER_API_KEY",
    "enabled": true,
    "priority": 0
  }'
```

Or define it in `.cognition/config.yaml` (bootstrapped on first startup):

```yaml
llm:
  provider: openai_compatible
  model: my-model
  base_url: https://my-provider.example.com/v1
  api_key_env: MY_PROVIDER_API_KEY
```

Test connectivity:

```bash
curl -X POST http://localhost:8000/models/providers/my-provider/test
```

For providers not supported by `init_chat_model`, wrap them in a LangChain `BaseChatModel` and use `openai_compatible` with a local proxy, or contribute a LangChain integration upstream.

---

## Hot-Reload

The file watcher (`server/app/file_watcher.py`) monitors `.cognition/middleware/` using `watchdog`. Agent definitions are reloaded through the config registry path. When watched files change:

1. The relevant registry/configuration path is refreshed.
2. Agent cache is invalidated so the next session uses the updated definition.

No server restart required. Changes typically take effect within 1 second.
