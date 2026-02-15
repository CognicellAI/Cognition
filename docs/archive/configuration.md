# Configuration Reference

Cognition uses a hierarchical configuration system that allows you to customize settings at multiple levels.

## Configuration Hierarchy

Configuration is loaded in the following order (later sources override earlier ones):

1. **Built-in defaults** (hardcoded in the application)
2. **Global user config** (`~/.cognition/config.yaml`)
3. **Project config** (`.cognition/config.yaml` in project directory)
4. **Environment variables** (highest precedence)

## Configuration File Locations

### Global Config

**Path**: `~/.cognition/config.yaml`

This file contains your personal default settings that apply to all projects.

```yaml
# Example ~/.cognition/config.yaml
server:
  host: 127.0.0.1
  port: 8000
  log_level: info

llm:
  provider: openai
  model: gpt-4o
  temperature: 0.7

workspace:
  root: ~/cognition-workspaces
```

### Project Config

**Path**: `.cognition/config.yaml` (in your project directory)

This file contains project-specific settings that override the global config.

```yaml
# Example .cognition/config.yaml in a project
llm:
  # Use Claude for this specific project
  provider: openai_compatible
  model: anthropic/claude-3-sonnet
  system_prompt: |
    You are an expert Python developer working on a Django project.
    Follow PEP 8 style guidelines and write comprehensive tests.

agent:
  max_iterations: 20
```

### Environment Variables

All settings can be configured via environment variables with the `COGNITION_` prefix:

```bash
export COGNITION_LLM_PROVIDER=openai
export COGNITION_LLM_MODEL=gpt-4o
export COGNITION_HOST=0.0.0.0
export COGNITION_PORT=8080
```

## Configuration Sections

### Server Settings

```yaml
server:
  host: "127.0.0.1"              # Server bind address
  port: 8000                      # Server port
  log_level: "info"               # Log level: debug, info, warning, error
  max_sessions: 100               # Maximum concurrent sessions
  session_timeout_seconds: 3600   # Session timeout (1 hour)
```

**Environment Variables**:
- `COGNITION_HOST`
- `COGNITION_PORT`
- `COGNITION_LOG_LEVEL`
- `COGNITION_MAX_SESSIONS`
- `COGNITION_SESSION_TIMEOUT_SECONDS`

### LLM Settings

```yaml
llm:
  provider: "openai"              # Provider: openai, bedrock, openai_compatible, mock
  model: "gpt-4o"                 # Model name
  temperature: 0.7                # Temperature (0.0 to 2.0)
  max_tokens: 4096                # Maximum tokens per response
  system_prompt: "..."            # Default system prompt
```

**Environment Variables**:
- `COGNITION_LLM_PROVIDER`
- `COGNITION_LLM_MODEL`
- `OPENAI_API_KEY` (for OpenAI provider)
- `OPENAI_API_BASE` (optional, for custom endpoints)
- `AWS_ACCESS_KEY_ID` (for Bedrock)
- `AWS_SECRET_ACCESS_KEY` (for Bedrock)
- `AWS_REGION` (for Bedrock)
- `COGNITION_OPENAI_COMPATIBLE_BASE_URL`
- `COGNITION_OPENAI_COMPATIBLE_API_KEY`

### Workspace Settings

```yaml
workspace:
  root: "./workspaces"            # Root directory for project workspaces
```

**Environment Variable**: `COGNITION_WORKSPACE_ROOT`

### Rate Limiting

```yaml
rate_limit:
  per_minute: 60                  # Requests per minute per client
  burst: 10                       # Burst allowance
```

**Environment Variables**:
- `COGNITION_RATE_LIMIT_PER_MINUTE`
- `COGNITION_RATE_LIMIT_BURST`

### Observability

```yaml
observability:
  otel_endpoint: null             # OpenTelemetry collector endpoint
  metrics_port: 9090              # Prometheus metrics port
```

**Environment Variables**:
- `COGNITION_OTEL_ENDPOINT`
- `COGNITION_METRICS_PORT`

## Provider-Specific Configuration

### OpenAI

```yaml
llm:
  provider: openai
  model: gpt-4o
```

Environment:
```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_API_BASE="https://api.openai.com/v1"  # Optional
```

### AWS Bedrock

```yaml
llm:
  provider: bedrock
  model: anthropic.claude-3-sonnet-20240229-v1:0
```

Environment:
```bash
export AWS_ACCESS_KEY_ID="AKIA..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-east-1"
```

### OpenAI-Compatible (e.g., OpenRouter, Ollama)

```yaml
llm:
  provider: openai_compatible
  model: google/gemini-pro
```

Environment:
```bash
export COGNITION_OPENAI_COMPATIBLE_BASE_URL="https://openrouter.ai/api/v1"
export COGNITION_OPENAI_COMPATIBLE_API_KEY="sk-or-..."
```

### Mock (for testing)

```yaml
llm:
  provider: mock
  model: mock-model
```

No API keys required.

## Configuration Examples

### Example 1: Personal Development Setup

`~/.cognition/config.yaml`:

```yaml
server:
  host: 127.0.0.1
  port: 8000
  log_level: info

llm:
  provider: openai
  model: gpt-4o
  temperature: 0.7

workspace:
  root: ~/workspace
```

`.env`:
```bash
OPENAI_API_KEY="sk-..."
```

### Example 2: Team Development with Different Models

`~/.cognition/config.yaml`:

```yaml
llm:
  provider: openai
  model: gpt-4o-mini  # Cheaper default
  temperature: 0.7
```

`project-a/.cognition/config.yaml`:

```yaml
llm:
  model: gpt-4o  # Use stronger model for complex project
  temperature: 0.5
  system_prompt: |
    You are working on a machine learning project.
    Use NumPy, pandas, and scikit-best practices.
```

`project-b/.cognition/config.yaml`:

```yaml
llm:
  provider: openai_compatible
  model: anthropic/claude-3-sonnet
```

### Example 3: Production Deployment

`~/.cognition/config.yaml`:

```yaml
server:
  host: 0.0.0.0
  port: 8000
  log_level: warning
  max_sessions: 500

llm:
  provider: bedrock
  model: anthropic.claude-3-sonnet-20240229-v1:0

rate_limit:
  per_minute: 120
  burst: 20

observability:
  otel_endpoint: http://otel-collector:4317
  metrics_port: 9090
```

## CLI Commands

### Initialize Configuration

```bash
# Create global config
cognition init --global

# Create project config
cognition init --project

# Create both
cognition init
```

### View Configuration

```bash
# Show merged configuration
cognition config

# Show only global config path
cognition config --global

# Show only project config path
cognition config --project
```

## Validation

Configuration is validated on server startup. Invalid configurations will cause the server to fail to start with a descriptive error message.

Common validation errors:

- **Invalid port**: Must be between 1 and 65535
- **Invalid temperature**: Must be between 0.0 and 2.0
- **Missing API key**: Required when using non-mock providers
- **Invalid provider**: Must be one of: openai, bedrock, openai_compatible, mock

## Migration

When upgrading Cognition, configuration files are automatically compatible. New settings use built-in defaults until explicitly configured.

## Security Notes

- Never commit API keys to version control
- Use `.env` files (ignored by git) for secrets
- Project configs (`.cognition/config.yaml`) should not contain secrets
- The `/config` API endpoint returns sanitized configuration (no secrets)
