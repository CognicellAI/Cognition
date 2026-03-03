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
| `server.max_sessions` | `COGNITION_MAX_SESSIONS` | `100` | Maximum concurrent active sessions |
| `server.session_timeout_seconds` | `COGNITION_SESSION_TIMEOUT_SECONDS` | `3600.0` | Session idle timeout in seconds |

---

## Workspace

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `workspace.root` | `COGNITION_WORKSPACE_ROOT` | `.` | Root directory for agent workspaces |

The workspace root is resolved to an absolute path at startup. The agent's tools operate within this directory.

---

## LLM Provider

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `llm.provider` | `COGNITION_LLM_PROVIDER` | `mock` | `openai`, `bedrock`, `openai_compatible`, `ollama`, `mock` |
| `llm.model` | `COGNITION_LLM_MODEL` | `gpt-4o` | Model identifier |
| `llm.temperature` | `COGNITION_LLM_TEMPERATURE` | `null` | 0.0–2.0; `null` uses provider default |
| `llm.max_tokens` | `COGNITION_LLM_MAX_TOKENS` | `null` | Max output tokens; `null` uses provider default |
| `llm.system_prompt` | `COGNITION_LLM_SYSTEM_PROMPT` | `null` | Override the default system prompt (inline text, `{file: name}`, or `{mlflow: name}`) |

### OpenAI

```env
OPENAI_API_KEY=sk-...
COGNITION_LLM_PROVIDER=openai
COGNITION_LLM_MODEL=gpt-4o
```

Optional custom base URL (for Azure OpenAI or proxies):

```env
OPENAI_API_BASE=https://myazure.openai.azure.com/
```

### AWS Bedrock

```env
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
COGNITION_LLM_PROVIDER=bedrock
COGNITION_LLM_MODEL=anthropic.claude-3-sonnet-20240229-v1:0
```

| YAML key | Environment variable | Default |
|---|---|---|
| `aws.region` | `AWS_REGION` | `us-east-1` |
| `bedrock.model_id` | `COGNITION_BEDROCK_MODEL_ID` | `anthropic.claude-3-sonnet-20240229-v1:0` |

### OpenAI-Compatible (OpenRouter, vLLM, LiteLLM, etc.)

```env
COGNITION_LLM_PROVIDER=openai_compatible
COGNITION_OPENAI_COMPATIBLE_BASE_URL=https://openrouter.ai/api/v1
COGNITION_OPENAI_COMPATIBLE_API_KEY=sk-or-...
COGNITION_LLM_MODEL=google/gemini-pro
```

### Ollama (local)

```env
COGNITION_LLM_PROVIDER=ollama
COGNITION_OLLAMA_BASE_URL=http://localhost:11434
COGNITION_OLLAMA_MODEL=llama3.2
```

### Mock (testing)

```env
COGNITION_LLM_PROVIDER=mock
```

No credentials required. Returns deterministic responses. Used by all unit tests.

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
| `security.tool_security` | `COGNITION_TOOL_SECURITY` | `warn` | `warn` (log only) or `strict` (block and error) |
| `security.protected_paths` | `COGNITION_PROTECTED_PATHS` | `[".cognition/"]` | Paths the agent cannot write to |
| `security.trusted_tool_namespaces` | `COGNITION_TRUSTED_TOOL_NAMESPACES` | `[]` | Allowed Python namespaces for tool imports; empty = allow all |
| `security.blocked_tools` | `COGNITION_BLOCKED_TOOLS` | `[]` | Tool names the agent cannot invoke |

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

## Prompt Registry

| YAML key | Environment variable | Default | Description |
|---|---|---|---|
| `prompt_registry.source` | `COGNITION_PROMPT_SOURCE` | `local` | `local` or `mlflow` |
| `prompt_registry.prompts_dir` | `COGNITION_PROMPTS_DIR` | `.cognition/prompts/` | Directory for local prompt files |
| `prompt_registry.fallback` | `COGNITION_PROMPT_FALLBACK` | `null` | Fallback prompt name if primary not found |

---

## Example: Development Setup

```yaml
# .cognition/config.yaml
server:
  host: 127.0.0.1
  port: 8000
  log_level: debug

llm:
  provider: openai
  model: gpt-4o
  temperature: 0.5

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
# .env
OPENAI_API_KEY=sk-...
```

## Example: Production Setup

```yaml
# .cognition/config.yaml
server:
  host: 0.0.0.0
  port: 8000
  log_level: info
  max_sessions: 500

llm:
  provider: bedrock
  model: anthropic.claude-3-sonnet-20240229-v1:0

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
  tool_security: strict
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
# .env (secrets only)
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
COGNITION_PERSISTENCE_URI=postgresql://cognition:secret@postgres:5432/cognition
```

---

## Runtime Configuration Changes

The `PATCH /config` endpoint allows updating a subset of settings at runtime without restarting:

**Allowed paths:** `llm.temperature`, `llm.max_tokens`, `llm.model`, `llm.provider`, `agent.memory`, `agent.skills`, `agent.interrupt_on`, `agent.subagents`, `rate_limit.per_minute`, `rate_limit.burst`, `observability.otel_enabled`, `observability.metrics_port`, `observability.otel_endpoint`, `mlflow.enabled`, `mlflow.experiment_name`.

Changes are persisted to `.cognition/config.yaml` and a backup is created at `.cognition/config.yaml.backup`. Roll back with `POST /config/rollback`.
