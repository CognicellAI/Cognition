# Configuration Reference

Cognition uses a 4-level configuration hierarchy. Higher levels override lower ones:

```
1. Built-in defaults       (hardcoded in server/app/settings.py)
2. Global YAML             (~/.cognition/config.yaml)
3. Project YAML            (.cognition/config.yaml — searched upward from CWD)
4. Environment variables   (highest precedence; overrides everything)
```

All `COGNITION_` environment variables map directly to settings fields. API keys and secrets should always be set via environment variables, never committed to YAML config files.

---

## Server

Controls the HTTP server.

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `server.host` | `COGNITION_HOST` | `127.0.0.1` | Bind address |
| `server.port` | `COGNITION_PORT` | `8000` | Listen port (1–65535) |
| `server.log_level` | `COGNITION_LOG_LEVEL` | `info` | `debug`, `info`, `warning`, `error` |

---

## Workspace

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `workspace.root` | `COGNITION_WORKSPACE_ROOT` | `.` | Root directory for agent workspaces |

The workspace root is resolved to an absolute path at startup. The agent's tools operate within this directory.

---

## LLM Provider Configuration

LLM provider and model settings are managed through the **ConfigRegistry**, a database-backed configuration store that supports hot-reloading. Provider configuration no longer lives in `Settings` or environment variables like `COGNITION_LLM_PROVIDER` / `COGNITION_LLM_MODEL`.

### Canonical runtime flow

Cognition does not pass raw provider selection policy into Deep Agents. Instead, it follows one canonical flow:

1. select a model target from session config, agent config, or provider registry
2. resolve provider-specific transport and credential fields
3. build a concrete LangChain chat model
4. pass that model into Deep Agents

This means Cognition owns configuration, validation, and LangChain model construction. Deep Agents owns execution.

### How it works

1. The `llm:` section in `.cognition/config.yaml` is **bootstrapped** into the ConfigRegistry on first startup using `seed_if_absent` — YAML values provide defaults, but rows written via the API always take precedence.
2. Providers can also be created, updated, and deleted at runtime via the REST API (`POST /models/providers`, `PATCH /models/providers/{id}`, `DELETE /models/providers/{id}`).
3. To list available models for a provider: `GET /models/providers/{id}/models`.
4. To verify credentials: `POST /models/providers/{id}/test`.
5. Sessions reference a provider via `SessionConfig.provider_id`.

### Runtime selection precedence

When Cognition resolves a model for a session, it uses this precedence order:

1. `SessionConfig.provider_id`
2. `SessionConfig.provider` + `SessionConfig.model`
3. `AgentDefinition.config.provider` + `AgentDefinition.config.model`
4. first enabled `ProviderConfig` by ascending `priority`

Recommended usage:

- prefer `provider_id` for stable runtime selection
- use `provider` + `model` only for direct transient overrides
- avoid model-only selection unless the mapping is unambiguous
- treat `/models` as a catalog view over configured provider types, not as the full global models.dev inventory

There is no silent provider fallback.

### Supported provider types

| Type | Description |
|---|---|
| `openai` | OpenAI API (GPT-4o, o1, etc.) |
| `anthropic` | Anthropic API (Claude 3.5, Claude 4, etc.) |
| `bedrock` | AWS Bedrock (any model available in your region) |
| `openai_compatible` | Any OpenAI-compatible endpoint (OpenRouter, vLLM, LiteLLM, Ollama, Azure OpenAI, etc.) |
| `google_genai` | Google Generative AI (Gemini) |
| `google_vertexai` | Google Vertex AI |
| `mock` | Test-only provider; skipped during bootstrap |

### Provider-specific validation

| Rule | Effect |
|---|---|
| `openai_compatible` requires `base_url` | Invalid config is rejected |
| Non-`openai_compatible` providers reject `base_url` | Prevents mismatched transport config |
| `bedrock` requires `region` | Invalid config is rejected |
| Non-`bedrock` providers reject `region` | Prevents provider-specific field drift |
| Non-`bedrock` providers reject `role_arn` | Prevents invalid cross-provider fields |

These rules apply to both create and update requests.

### config.yaml `llm:` section format

```yaml
# .cognition/config.yaml
llm:
  - provider: openai
    model: gpt-4o

  - provider: anthropic
    model: claude-sonnet-4-20250514

  - provider: bedrock
    model: anthropic.claude-3-sonnet-20240229-v1:0
    region: us-east-1
    role_arn: arn:aws:iam::123456789012:role/BedrockAccess  # optional

  - provider: openai_compatible
    model: google/gemini-pro
    base_url: https://openrouter.ai/api/v1
    api_key_env: COGNITION_OPENAI_COMPATIBLE_API_KEY

  - provider: google_genai
    model: gemini-1.5-pro

  - provider: google_vertexai
    model: gemini-1.5-pro
    region: us-central1
```

Each entry supports the following fields:

| Field | Required | Description |
|---|---|---|
| `provider` | Yes | One of the supported provider types above |
| `model` | Yes | Model identifier (provider-specific) |
| `base_url` | No | Custom API endpoint (required for `openai_compatible`) |
| `api_key_env` | No | Name of the environment variable holding the API key |
| `region` | No | AWS region (`bedrock`) or GCP region (`google_vertexai`) |
| `role_arn` | No | AWS IAM role ARN for cross-account Bedrock access |

The `llm:` section is a bootstrap surface, not the long-term source of truth. After startup, the effective provider registry lives in the ConfigRegistry and can be changed through the API without restart.

For production systems and user-facing applications, prefer API-managed provider configs and bind sessions by `provider_id`.

### Credential environment variables

| Provider | Environment variable |
|---|---|
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `bedrock` | AWS IAM credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`) |
| `openai_compatible` | `COGNITION_OPENAI_COMPATIBLE_API_KEY` (or custom via `api_key_env`) |
| `google_genai` | `GOOGLE_API_KEY` |
| `google_vertexai` | GCP Application Default Credentials |

### Provider examples

**OpenAI:**

```yaml
llm:
  - provider: openai
    model: gpt-4o
```

```bash
OPENAI_API_KEY=sk-...
```

**Anthropic:**

```yaml
llm:
  - provider: anthropic
    model: claude-sonnet-4-20250514
```

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

**AWS Bedrock:**

```yaml
llm:
  - provider: bedrock
    model: anthropic.claude-3-sonnet-20240229-v1:0
    region: us-east-1
```

```bash
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
```

**OpenAI-Compatible (OpenRouter, vLLM, LiteLLM, Ollama, etc.):**

```yaml
llm:
  - provider: openai_compatible
    model: google/gemini-pro
    base_url: https://openrouter.ai/api/v1
    api_key_env: COGNITION_OPENAI_COMPATIBLE_API_KEY
```

```bash
COGNITION_OPENAI_COMPATIBLE_API_KEY=sk-or-...
```

To use a local Ollama instance, configure it as an `openai_compatible` provider:

```yaml
llm:
  - provider: openai_compatible
    model: llama3.2
    base_url: http://localhost:11434/v1
```

**Google Generative AI:**

```yaml
llm:
  - provider: google_genai
    model: gemini-1.5-pro
```

```bash
GOOGLE_API_KEY=AI...
```

**Google Vertex AI:**

```yaml
llm:
  - provider: google_vertexai
    model: gemini-1.5-pro
    region: us-central1
```

Requires GCP Application Default Credentials to be configured.

**Mock (testing only):**

No credentials required. Returns deterministic responses. Used by unit tests. The `mock` provider is skipped during bootstrap and cannot be seeded from config.yaml.

### Session config guidance

`SessionConfig` can set `provider_id`, `provider`, `model`, `temperature`, `max_tokens`, and `recursion_limit`.

Important rules:

- `provider_id` is the safest and most explicit selector
- `provider` requires `model`
- `model` by itself is only accepted when it matches exactly one enabled provider type
- if model-only selection is ambiguous or unknown, Cognition returns `422` instead of picking a provider silently

---

## Persistence

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `persistence.backend` | `COGNITION_PERSISTENCE_BACKEND` | `sqlite` | `sqlite`, `postgres`, `memory` |
| `persistence.uri` | `COGNITION_PERSISTENCE_URI` | `.cognition/state.db` | File path (SQLite) or connection string (Postgres) |

**PostgreSQL connection string format:**

```env
COGNITION_PERSISTENCE_BACKEND=postgres
COGNITION_PERSISTENCE_URI=postgresql://user:password@host:5432/dbname
```

**Note:** An unknown `persistence.backend` value raises `StorageBackendError` at startup — there is no silent fallback to SQLite.

---

## Sandbox (Execution)

Cognition ships four sandbox backends:

| Backend | Isolation | Works on K8s? |
|---|---|---|
| `local` | None — commands run as server process user | Yes, but no isolation |
| `docker` | Container per session | No — requires Docker socket + privileged mode |
| `kubernetes` | Sandbox pod per session | Yes — K8s-native, no special privileges needed |
| `aws_lambda_microvm` | AWS Lambda MicroVM per sandbox runtime | Yes — if Cognition has AWS credentials |

### Common settings

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `sandbox.backend` | `COGNITION_SANDBOX_BACKEND` | `local` | `local`, `docker`, `kubernetes`, or `aws_lambda_microvm` |

### Docker settings (when `sandbox.backend = docker`)

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `sandbox.docker_image` | `COGNITION_DOCKER_IMAGE` | `cognition-sandbox:latest` | Docker image for the sandbox container |
| `sandbox.docker_network` | `COGNITION_DOCKER_NETWORK` | `none` | Container network mode |
| `sandbox.docker_timeout` | `COGNITION_DOCKER_TIMEOUT` | `300` | Command execution timeout in seconds |
| `sandbox.docker_memory_limit` | `COGNITION_DOCKER_MEMORY_LIMIT` | `512m` | Container memory limit |
| `sandbox.docker_cpu_limit` | `COGNITION_DOCKER_CPU_LIMIT` | `1.0` | Container CPU limit (cores) |
| `sandbox.docker_host_workspace` | `COGNITION_DOCKER_HOST_WORKSPACE` | `null` | Host path to mount into the container |

### Kubernetes settings (when `sandbox.backend = kubernetes`)

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `sandbox.k8s.template` | `COGNITION_K8S_SANDBOX_TEMPLATE` | `cognition-sandbox` | SandboxTemplate CR name defining the sandbox pod spec |
| `sandbox.k8s.namespace` | `COGNITION_K8S_SANDBOX_NAMESPACE` | `default` | Kubernetes namespace for sandbox CRs |
| `sandbox.k8s.router_url` | `COGNITION_K8S_SANDBOX_ROUTER_URL` | `http://sandbox-router-svc.default.svc.cluster.local:8080` | sandbox-router service URL |
| `sandbox.k8s.ttl` | `COGNITION_K8S_SANDBOX_TTL` | `3600` | Auto-cleanup after N seconds (safety net for abandoned sandboxes) |
| `sandbox.k8s.warm_pool` | `COGNITION_K8S_SANDBOX_WARM_POOL` | (none) | SandboxWarmPool CR name (reserved, not yet implemented) |

See [Kubernetes Sandbox](../concepts/sandboxes/kubernetes/index.md) for
architecture, prerequisites, and deployment details.

### AWS Lambda MicroVM settings (when `sandbox.backend = aws_lambda_microvm`)

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| (environment only) | `COGNITION_AWS_LAMBDA_MICROVM_DEFAULT_PROFILE` | `default` | SandboxProfile name used when an agent does not specify `sandbox_profile` |

Lambda MicroVM profiles are managed through the `sandbox_profiles:` YAML
section or the `/sandbox/profiles` API. File-seeded profiles are inserted only
when absent; API-managed profiles take precedence after startup.

```yaml
sandbox:
  backend: aws_lambda_microvm

sandbox_profiles:
  default-lambda:
    backend: aws_lambda_microvm
    image_arn: arn:aws:lambda:us-west-2:123456789012:microvm-image:cognition-runtime
    image_version: "1.0"
    region: us-west-2
    egress_mode: internet
    maximum_duration_seconds: 3600
    port: 8080
    token_expiration_minutes: 30
    idle_policy:
      max_idle_duration_seconds: 900
      suspended_duration_seconds: 300
      auto_resume_enabled: true
    logging:
      disabled: {}
    quota:
      max_concurrent_sessions: 10
      max_session_starts_per_minute: 30
    default_execution_role_arn: arn:aws:iam::123456789012:role/cognition-agent-runtime
```

For private egress, set `egress_mode: vpc` and provide explicit
`egress_network_connector_arns`.

Cost-sensitive Lambda MicroVM profile keys:

| Key | Cost impact |
|---|---|
| `maximum_duration_seconds` | Hard upper bound on billable MicroVM lifetime |
| `idle_policy` | Allows AWS to suspend idle MicroVMs instead of continuing active compute |
| `logging` | CloudWatch runtime logs may incur ingestion and retention cost; use `disabled: {}` unless needed |
| `quota` | Cognition-side cap on concurrent sandbox sessions and starts per minute for a profile/scope pair |
| `egress_mode` / `egress_network_connector_arns` | VPC connectors can add network path and data-transfer costs |
| `run_hook_payload` | Can trigger runtime image hook work during launch; keep hook behavior bounded |

```yaml
sandbox_profiles:
  private-lambda:
    backend: aws_lambda_microvm
    image_arn: arn:aws:lambda:us-west-2:123456789012:microvm-image:cognition-runtime
    region: us-west-2
    egress_mode: vpc
    egress_network_connector_arns:
      - arn:aws:lambda:us-west-2:123456789012:network-connector:private-egress
```

Agents select profiles and execution roles from trusted config:

```yaml
agents:
  - name: repo-maintainer
    system_prompt: "You maintain Python repositories."
    sandbox_profile: default-lambda
    sandbox_execution_role_arn: arn:aws:iam::123456789012:role/repo-maintainer-runtime
```

`sandbox_execution_role_arn` overrides the profile default role for that
agent. The role is never read from model-generated tool arguments.

See [AWS Lambda MicroVM Setup](../concepts/sandboxes/aws-lambda-microvm/setup.md)
for the end-to-end setup flow and Terraform example.

---

## Rate Limiting

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `rate_limit.per_minute` | `COGNITION_RATE_LIMIT_PER_MINUTE` | `60` | Requests per minute per scope key |
| `rate_limit.burst` | `COGNITION_RATE_LIMIT_BURST` | `10` | Burst allowance above the per-minute rate |

---

## Observability

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `observability.otel_enabled` | `COGNITION_OTEL_ENABLED` | `true` | Enable OpenTelemetry tracing |
| `observability.otel_endpoint` | `COGNITION_OTEL_ENDPOINT` | `null` | OTLP collector URL |
| `observability.metrics_port` | `COGNITION_METRICS_PORT` | `9090` | Prometheus metrics scrape port |

---

## MLflow

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `mlflow.enabled` | `COGNITION_MLFLOW_ENABLED` | `false` | Enable MLflow experiment tracking |
| `mlflow.tracking_uri` | `COGNITION_MLFLOW_TRACKING_URI` | `null` | MLflow server URL |
| `mlflow.experiment_name` | `COGNITION_MLFLOW_EXPERIMENT_NAME` | `cognition` | MLflow experiment name |

---

## CORS

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `cors.origins` | `COGNITION_CORS_ORIGINS` | `["*"]` | Allowed origins (JSON array or comma-separated) |
| `cors.methods` | `COGNITION_CORS_METHODS` | `["*"]` | Allowed HTTP methods |
| `cors.headers` | `COGNITION_CORS_HEADERS` | `["*"]` | Allowed request headers |
| `cors.allow_credentials` | `COGNITION_CORS_ALLOW_CREDENTIALS` | `false` | Allow cookies and credentials |

---

## Security

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `security.protected_paths` | `COGNITION_PROTECTED_PATHS` | `[".cognition/"]` | Paths the agent cannot write to |
| `security.trusted_tool_namespaces` | `COGNITION_TRUSTED_TOOL_NAMESPACES` | `[]` | Allowed Python namespaces for tool imports; empty = allow all |
| `security.blocked_tools` | `COGNITION_BLOCKED_TOOLS` | `[]` | Deployment-wide tool names no agent can invoke; merged with per-agent `blocked_tools` and enforced by `ToolSecurityMiddleware` |
| `security.a2a_enabled` | `COGNITION_A2A_ENABLED` | `true` | Enable/disable the A2A protocol adapter (`/.well-known/agent-card.json` + `/a2a/{agent_name}`) |

> **Note:** `COGNITION_TOOL_SECURITY` (`warn`/`strict`) was removed. AST scanning has been replaced with Gateway-level authorization. See [Security concepts](../concepts/security.md) for the current trust model.

`COGNITION_BLOCKED_TOOLS` is an execution-deny policy only. It does not remove tools from the model-visible schema. To hide tools for a specific agent, set `config.excluded_tools` on that agent definition or pass `excluded_tools` through the `/agents` API.

---

## Session Scoping (Multi-Tenancy)

Scope keys are **builder-defined** — Cognition does not hardcode a vocabulary. Choose keys that match your application's tenancy model.

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `scoping.enabled` | `COGNITION_SCOPING_ENABLED` | `false` | Enable scope header enforcement |
| `scoping.scope_keys` | `COGNITION_SCOPE_KEYS` | `["user"]` | Required scope key names (builder-defined; each key requires a matching `X-Cognition-Scope-{key}` header) |

---

## SSE Streaming

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `sse.retry_interval` | `COGNITION_SSE_RETRY_INTERVAL` | `3000` | Reconnection hint sent to clients (ms) |
| `sse.heartbeat_interval` | `COGNITION_SSE_HEARTBEAT_INTERVAL` | `15.0` | Heartbeat comment interval (seconds) |
| `sse.buffer_size` | `COGNITION_SSE_BUFFER_SIZE` | `100` | Event buffer size for reconnection replay |

---

## Agent Defaults

These settings configure the default agent behaviour when no `AgentDefinition` overrides them.

| YAML key | Description |
|---|---|
| `agent.memory` | List of file paths injected into the system prompt (e.g. `["AGENTS.md"]`) |
| `agent.skills` | List of attached skill names (registry names, not paths) |
| `agent.subagents` | List of subagent definitions |
| `agent.interrupt_on` | Map of tool names to `true`/`false` for human-in-the-loop confirmation |
| `agent.middleware` | List of middleware names or `{name: ..., **kwargs}` dicts |
| `agent.sandbox_profile` | Default Lambda MicroVM sandbox profile for the file-defined default agent |
| `agent.sandbox_execution_role_arn` | Trusted IAM execution role ARN for the file-defined default agent sandbox |

Per-agent runtime config can also set `config.excluded_tools` and `config.blocked_tools`. File-based agent definitions place those values under `config:`; the `/agents` API accepts the same policies as top-level fields and returns them under `config`.

There is no global `excluded_tools` setting in v0.10.4. Use per-agent exclusions when one agent should have a narrower tool surface than another.

**Upstream middleware names** (usable in `agent.middleware`):

| Name | Parameters | Description |
|---|---|---|
| `tool_retry` | `max_retries`, `backoff_factor` | Exponential backoff on tool failure |
| `tool_call_limit` | `run_limit`, `thread_limit`, `per_tool_limits` | Per-tool and global call ceilings |
| `pii` | `pii_types`, `strategy` | Detect and redact PII (email, phone, credit card, IP, SSN) |
| `human_in_the_loop` | `approve_tools` | Require human approval before specified tools execute |

### Provider and Model Resolution Hierarchy

Provider and model selection follow a strict priority chain (highest to lowest):

1. **`SessionConfig.provider_id`** — exact `ProviderConfig` lookup by ID from ConfigRegistry  
2. **`SessionConfig.provider` + `SessionConfig.model`** — direct per-session override  
3. **`AgentDefinition.config.provider` + `.model`** — per-agent definition override  
4. **First enabled `ProviderConfig` from ConfigRegistry** — sorted by `priority` (ascending)

`recursion_limit` and `temperature` follow the same chain: session > agent definition > ConfigRegistry default.

If no provider is found at any tier, `LLMProviderConfigError` is raised with an actionable message — there is no silent fallback.

---

## Agent MCP Servers

MCP servers belong to an Agent definition and are pinned with its active
configuration revision. Cognition has no global MCP-server registry or
`/mcp-servers` CRUD API.

```yaml
mcp_servers:
  - alias: github
    url: https://mcp.github.example.com/mcp
    transport: streamable_http
    required: true
    auth:
      type: outbound_auth_provider
      profile: production-egress
```

Only remote HTTP/HTTPS Streamable HTTP endpoints are supported. The
configuration cannot contain raw headers, credentials, callback code, or a
model-controlled endpoint. Tools have the canonical identity
`(server_alias, provider_tool_name)`; the model-visible name is derived from it.

| Authentication type | Use |
|---|---|
| `none` | A server that requires no transport authentication. |
| `mcp_oauth` | Standard MCP OAuth for a direct provider endpoint. |
| `outbound_auth_provider` | Optional builder-installed authentication or egress policy using an opaque profile. |
| `static_bearer` | Local/development only; reads a token from a named environment variable. |

`outbound_auth_provider` is not required for direct endpoints. It is the
extension point for builders that need gateway authorization, token exchange,
or credential brokering without making Cognition a credential vault. See the
[v0.14 MCP runtime contract](../proposals/v0.14.0-mcp-runtime-contract.md).

---

## Model Catalog

Cognition integrates with [models.dev](https://models.dev) to provide enriched model metadata including context windows, tool call support, pricing, and modalities.

| Environment variable | Default | Description |
|---|---|---|
| `COGNITION_MODEL_CATALOG_URL` | `https://models.dev/api.json` | URL for the model catalog data source |
| `COGNITION_MODEL_CATALOG_TTL_SECONDS` | `3600` | Cache TTL for model catalog data (seconds) |

---

## Example: Development Setup

```yaml
# .cognition/config.yaml
server:
  host: 127.0.0.1
  port: 8000
  log_level: debug

llm:
  - provider: openai
    model: gpt-4o

persistence:
  backend: sqlite
  uri: .cognition/state.db

agent:
  memory:
    - "AGENTS.md"
  skills:
    - "my-skill-name"
```

```bash
# .env — API keys are set via env vars; the config.yaml llm: section seeds the ConfigRegistry
OPENAI_API_KEY=sk-...
```

## Example: Production Setup

```yaml
# .cognition/config.yaml
server:
  host: 0.0.0.0
  port: 8000
  log_level: info

llm:
  - provider: bedrock
    model: anthropic.claude-3-sonnet-20240229-v1:0
    region: us-east-1

  - provider: anthropic
    model: claude-sonnet-4-20250514

persistence:
  backend: postgres

sandbox:
  backend: docker
  docker_network: none
  docker_memory_limit: 1g
  docker_cpu_limit: 2.0

observability:
  otel_enabled: true
  otel_endpoint: http://otel-collector:4317
  metrics_port: 9090

mlflow:
  enabled: true
  tracking_uri: http://mlflow:5000

security:
  trusted_tool_namespaces:
    - "myapp.tools"

scoping:
  enabled: true
  scope_keys:
    - "user"
    - "project"

# A2A is auto-mounted when enabled. No configuration needed.
# Expose agents via A2A by setting a2a_exposed: true on their definition.
# Set COGNITION_A2A_ENABLED=false to disable the A2A protocol surface entirely.

rate_limit:
  per_minute: 120
  burst: 20
```

```bash
# .env — secrets only; the config.yaml llm: section seeds the ConfigRegistry on first startup
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
ANTHROPIC_API_KEY=sk-ant-...
COGNITION_PERSISTENCE_URI=postgresql://cognition:secret@postgres:5432/cognition
```

## Example: Kubernetes Sandbox Setup

```yaml
# .cognition/config.yaml
server:
  host: 0.0.0.0
  port: 8000

llm:
  - provider: openai_compatible
    model: google/gemini-3-flash-preview
    base_url: https://openrouter.ai/api/v1

persistence:
  backend: postgres

sandbox:
  backend: kubernetes
  k8s:
    template: cognition-sandbox
    namespace: cognition
    router_url: http://sandbox-router-svc.cognition.svc.cluster.local:8080
    ttl: 3600

scoping:
  enabled: true
  scope_keys:
    - "user"
```

```bash
# .env
COGNITION_OPENAI_COMPATIBLE_API_KEY=sk-or-v1-...
COGNITION_PERSISTENCE_URI=postgresql+asyncpg://cognition:secret@cognition-db-rw:5432/cognition
```

---

## Runtime Configuration Changes

LLM provider and agent configuration is now managed via the **ConfigRegistry API** (`POST/PATCH/DELETE /models/providers`), not the `PATCH /config` endpoint. Changes made through the ConfigRegistry are hot-reloaded and always take precedence over config.yaml seed values.

The `PATCH /config` endpoint is restricted to **infrastructure settings only**:

**Allowed paths:** `rate_limit.per_minute`, `rate_limit.burst`, `observability.otel_enabled`, `observability.metrics_port`, `observability.otel_endpoint`, `mlflow.enabled`, `mlflow.experiment_name`.

Changes are persisted to `.cognition/config.yaml` and a backup is created at `.cognition/config.yaml.backup`. Roll back with `POST /config/rollback`.
