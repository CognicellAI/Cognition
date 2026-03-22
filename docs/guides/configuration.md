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

### How it works

1. The `llm:` section in `.cognition/config.yaml` is **bootstrapped** into the ConfigRegistry on first startup using `seed_if_absent` — YAML values provide defaults, but rows written via the API always take precedence.
2. Providers can also be created, updated, and deleted at runtime via the REST API (`POST /models/providers`, `PATCH /models/providers/{id}`, `DELETE /models/providers/{id}`).
3. To list available models for a provider: `GET /models/providers/{id}/models`.
4. To verify credentials: `POST /models/providers/{id}/test`.
5. Sessions reference a provider via `SessionConfig.provider_id`.

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

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `sandbox.backend` | `COGNITION_SANDBOX_BACKEND` | `local` | `local` or `docker` |
| `sandbox.docker_image` | `COGNITION_DOCKER_IMAGE` | `cognition-sandbox:latest` | Docker image for the sandbox container |
| `sandbox.docker_network` | `COGNITION_DOCKER_NETWORK` | `none` | Container network mode |
| `sandbox.docker_timeout` | `COGNITION_DOCKER_TIMEOUT` | `300` | Command execution timeout in seconds |
| `sandbox.docker_memory_limit` | `COGNITION_DOCKER_MEMORY_LIMIT` | `512m` | Container memory limit |
| `sandbox.docker_cpu_limit` | `COGNITION_DOCKER_CPU_LIMIT` | `1.0` | Container CPU limit (cores) |
| `sandbox.docker_host_workspace` | `COGNITION_DOCKER_HOST_WORKSPACE` | `null` | Host path to mount into the container |

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
| `security.blocked_tools` | `COGNITION_BLOCKED_TOOLS` | `[]` | Tool names the agent cannot invoke (enforced by `ToolSecurityMiddleware`) |

> **Note:** `COGNITION_TOOL_SECURITY` (`warn`/`strict`) was removed. AST scanning has been replaced with Gateway-level authorization. See [Security concepts](../concepts/security.md) for the current trust model.

---

## Session Scoping (Multi-Tenancy)

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `scoping.enabled` | `COGNITION_SCOPING_ENABLED` | `false` | Enable scope header enforcement |
| `scoping.scope_keys` | `COGNITION_SCOPE_KEYS` | `["user"]` | Required scope dimensions |

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
| `agent.skills` | List of skill directories or SKILL.md files |
| `agent.subagents` | List of subagent definitions |
| `agent.interrupt_on` | Map of tool names to `true`/`false` for human-in-the-loop confirmation |
| `agent.middleware` | List of middleware names or `{name: ..., **kwargs}` dicts |

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

## MCP Remote Servers

```yaml
# .cognition/config.yaml
mcp:
  servers:
    - name: my-tools
      url: https://tools.example.com/sse
```

Each server must be an HTTP/HTTPS SSE endpoint. Stdio-based MCP servers are not supported for security reasons.

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
    - ".cognition/skills/"
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

---

## Runtime Configuration Changes

LLM provider and agent configuration is now managed via the **ConfigRegistry API** (`POST/PATCH/DELETE /models/providers`), not the `PATCH /config` endpoint. Changes made through the ConfigRegistry are hot-reloaded and always take precedence over config.yaml seed values.

The `PATCH /config` endpoint is restricted to **infrastructure settings only**:

**Allowed paths:** `rate_limit.per_minute`, `rate_limit.burst`, `observability.otel_enabled`, `observability.metrics_port`, `observability.otel_endpoint`, `mlflow.enabled`, `mlflow.experiment_name`.

Changes are persisted to `.cognition/config.yaml` and a backup is created at `.cognition/config.yaml.backup`. Roll back with `POST /config/rollback`.
