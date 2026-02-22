# Configuration Reference

Cognition uses a 4-layer configuration hierarchy (lowest to highest precedence):

1. **Built-in defaults** (hardcoded in `Settings` class)
2. **Global YAML:** `~/.cognition/config.yaml`
3. **Project YAML:** `.cognition/config.yaml` (searched from CWD upward)
4. **Environment variables / `.env`** (highest precedence)

## Environment Variables

All environment variables use the `COGNITION_` prefix (except standard ones like `OPENAI_API_KEY`).

### LLM Providers

| Variable | Default | Description |
|----------|---------|-------------|
| `COGNITION_LLM_PROVIDER` | `mock` | `mock`, `openai`, `bedrock`, `openai_compatible`, `ollama` |
| `COGNITION_LLM_MODEL` | `gpt-4o` | Model identifier |
| `COGNITION_LLM_TEMPERATURE` | `null` | 0.0-2.0 (null = provider default) |
| `COGNITION_LLM_MAX_TOKENS` | `null` | Max output tokens |

#### Provider Specifics

**OpenAI:**
```env
OPENAI_API_KEY=sk-...
```

**AWS Bedrock:**
```env
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
```

**Ollama:**
```env
COGNITION_OLLAMA_BASE_URL=http://localhost:11434
COGNITION_OLLAMA_MODEL=llama3.2
```

### Persistence

| Variable | Default | Description |
|----------|---------|-------------|
| `COGNITION_PERSISTENCE_BACKEND` | `sqlite` | `sqlite`, `postgres`, `memory` |
| `COGNITION_PERSISTENCE_URI` | `.cognition/state.db` | File path or connection string |

**PostgreSQL Example:**
```env
COGNITION_PERSISTENCE_URI=postgresql://user:pass@host:5432/cognition
```

### Execution & Sandbox

| Variable | Default | Description |
|----------|---------|-------------|
| `COGNITION_SANDBOX_BACKEND` | `local` | `local`, `docker` |
| `COGNITION_DOCKER_IMAGE` | `cognition-sandbox:latest` | Sandbox image name |
| `COGNITION_DOCKER_MEMORY_LIMIT` | `512m` | Container memory limit |
| `COGNITION_DOCKER_CPU_LIMIT` | `1.0` | Container CPU limit |
| `COGNITION_DOCKER_NETWORK` | `none` | Network mode (`none` = isolated) |

### Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `COGNITION_OTEL_ENABLED` | `true` | Enable OpenTelemetry tracing |
| `COGNITION_OTEL_ENDPOINT` | `null` | Collector URL (e.g., `http://localhost:4317`) |
| `COGNITION_MLFLOW_ENABLED` | `false` | Enable MLflow tracking |
| `COGNITION_MLFLOW_TRACKING_URI` | `null` | MLflow server URL |
| `COGNITION_METRICS_PORT` | `9090` | Prometheus metrics port |

### Multi-Tenancy

| Variable | Default | Description |
|----------|---------|-------------|
| `COGNITION_SCOPING_ENABLED` | `false` | Enable scope validation |
| `COGNITION_SCOPE_KEYS` | `["user"]` | JSON list of required scope keys |

## YAML Configuration

Example `.cognition/config.yaml`:

```yaml
server:
  host: 0.0.0.0
  port: 8080
  log_level: info

llm:
  provider: openai
  model: gpt-4o

agent:
  memory:
    - "AGENTS.md"
  skills:
    - ".cognition/skills/"
  interrupt_on:
    execute: true  # Confirm shell commands
```
