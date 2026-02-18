# Configuration Guide

Complete reference for configuring Cognition via YAML config files and environment variables.

## Configuration Hierarchy

Cognition uses a hierarchical configuration system (lowest to highest precedence):

1. **Built-in defaults** - Hardcoded in `Settings` class
2. **`~/.cognition/config.yaml`** - Global user preferences
3. **`.cognition/config.yaml`** - Project-level overrides
4. **Environment variables / `.env`** - Highest precedence
5. **CLI flags** - Runtime overrides (`--host`, `--port`, `--log-level`)

## Quick Start

Create a project config:

```bash
mkdir -p .cognition
cp config.example.yaml .cognition/config.yaml
```

Edit `.cognition/config.yaml` and customize for your project.

## Configuration Sections

### Server Settings (`server`)

Configure the HTTP server that serves the Cognition API.

```yaml
server:
  host: "127.0.0.1"        # Bind address (use "0.0.0.0" for Docker)
  port: 8000               # Server port
  log_level: "info"        # Logging level: debug, info, warning, error
  max_sessions: 100        # Maximum concurrent sessions
  session_timeout_seconds: 3600  # Session inactivity timeout
```

**Environment Variables:**
- `COGNITION_HOST` - Server bind address
- `COGNITION_PORT` - Server port
- `COGNITION_LOG_LEVEL` - Logging level
- `COGNITION_MAX_SESSIONS` - Max concurrent sessions
- `COGNITION_SESSION_TIMEOUT_SECONDS` - Session timeout

**Common Use Cases:**

**Development (local):**
```yaml
server:
  host: "127.0.0.1"
  port: 8000
  log_level: "debug"
```

**Docker/Production:**
```yaml
server:
  host: "0.0.0.0"        # Bind to all interfaces
  port: 8000
  log_level: "warning"   # Less verbose in production
```

### Workspace Settings (`workspace`)

Configure the base directory for project workspaces.

```yaml
workspace:
  root: "."              # Path to workspace root (absolute or relative)
```

**Environment Variable:**
- `COGNITION_WORKSPACE_ROOT` - Workspace root directory

**Notes:**
- If relative, resolved from the directory where the server starts
- Each session gets a subdirectory within this root
- Default `.` means current working directory

### LLM Settings (`llm`)

Configure the AI model provider and behavior.

```yaml
llm:
  provider: "mock"              # Provider: mock, openai, bedrock, openai_compatible, ollama
  model: "gpt-4o"              # Model name (provider-specific)
  temperature: null            # Sampling temperature (0-2, null for default)
  max_tokens: null             # Max output tokens (null for default)
  system_prompt: null          # Custom system prompt override
```

**Environment Variables:**
- `COGNITION_LLM_PROVIDER` - LLM provider
- `COGNITION_LLM_MODEL` - Model name
- `COGNITION_LLM_TEMPERATURE` - Temperature
- `COGNITION_LLM_MAX_TOKENS` - Max tokens
- `COGNITION_LLM_SYSTEM_PROMPT` - System prompt

#### Provider-Specific Settings

**OpenAI (`openai`):**

```yaml
openai:
  api_base: null               # Custom API base URL (optional)
```

**Environment Variables:**
- `OPENAI_API_KEY` - **Required** - Your OpenAI API key
- `OPENAI_API_BASE` - Optional custom base URL

**AWS Bedrock (`aws`, `bedrock`):**

```yaml
aws:
  region: "us-east-1"          # AWS region

bedrock:
  model_id: "anthropic.claude-3-sonnet-20240229-v1:0"  # Bedrock model ID
```

**Environment Variables:**
- `AWS_ACCESS_KEY_ID` - AWS access key
- `AWS_SECRET_ACCESS_KEY` - AWS secret key
- `AWS_REGION` - AWS region (defaults to us-east-1)
- `COGNITION_BEDROCK_MODEL_ID` - Model ID

**OpenAI-Compatible (`openai_compatible`):**

For self-hosted models (vLLM, LM Studio, etc.):

```yaml
openai_compatible:
  base_url: "http://localhost:8000/v1"  # API endpoint
```

**Environment Variables:**
- `COGNITION_OPENAI_COMPATIBLE_BASE_URL` - API base URL
- `COGNITION_OPENAI_COMPATIBLE_API_KEY` - API key (if required)

**Ollama (`ollama`):**

For local models via Ollama:

```yaml
ollama:
  model: "llama3.2"                    # Model name
  base_url: "http://localhost:11434"   # Ollama server URL
```

**Environment Variables:**
- `COGNITION_OLLAMA_MODEL` - Model name
- `COGNITION_OLLAMA_BASE_URL` - Ollama server URL

#### Provider Selection Examples

**Using OpenAI GPT-4:**
```yaml
llm:
  provider: "openai"
  model: "gpt-4o"
  temperature: 0.7
```
```bash
export OPENAI_API_KEY="sk-..."
```

**Using AWS Bedrock:**
```yaml
llm:
  provider: "bedrock"
  model: "anthropic.claude-3-sonnet"
```
```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-west-2"
```

**Using Local Ollama:**
```yaml
llm:
  provider: "ollama"
  model: "llama3.2"
```

**Using Self-Hosted vLLM:**
```yaml
llm:
  provider: "openai_compatible"
  model: "meta-llama/Llama-2-70b"

openai_compatible:
  base_url: "http://vllm-server:8000/v1"
```

### Rate Limiting (`rate_limit`)

Control API request throttling.

```yaml
rate_limit:
  per_minute: 60    # Requests per minute
  burst: 10         # Burst capacity
```

**Environment Variables:**
- `COGNITION_RATE_LIMIT_PER_MINUTE` - Rate limit
- `COGNITION_RATE_LIMIT_BURST` - Burst capacity

**How it works:**
- Token bucket algorithm
- `per_minute`: Steady-state request rate
- `burst`: Maximum requests in a short burst
- Example: 60/min with burst 10 allows 10 requests immediately, then 1 per second

### Observability (`observability`)

Configure metrics, tracing, and monitoring.

```yaml
observability:
  otel_endpoint: null      # OpenTelemetry endpoint (e.g., "http://localhost:4317")
  metrics_port: 9090       # Prometheus metrics port
```

**Environment Variables:**
- `COGNITION_OTEL_ENDPOINT` - OpenTelemetry collector endpoint
- `COGNITION_METRICS_PORT` - Prometheus metrics port

**Use Cases:**

**With Prometheus:**
```yaml
observability:
  metrics_port: 9090
```
Metrics available at `http://localhost:9090/metrics`

**With OpenTelemetry (Jaeger/Grafana):**
```yaml
observability:
  otel_endpoint: "http://jaeger:4317"
  metrics_port: 9090
```

### Agent Behavior (`agent`)

Configure how the AI agent behaves and what capabilities it has.

```yaml
agent:
  memory:                    # Memory files to load
    - "AGENTS.md"            # Project-specific instructions
  
  skills:                    # Skill directories to scan
    - ".cognition/skills/"   # Project skills
  
  subagents: []              # Subagent configurations
  
  interrupt_on: {}           # Tools requiring human confirmation
```

**Environment Variables:**
- `COGNITION_AGENT_MEMORY` - JSON list of memory files
- `COGNITION_AGENT_SKILLS` - JSON list of skill directories
- `COGNITION_AGENT_SUBAGENTS` - JSON list of subagent configs
- `COGNITION_AGENT_INTERRUPT_ON` - JSON dict of interrupt settings

#### Memory Files

Memory files contain project-specific instructions and context:

```yaml
agent:
  memory:
    - "AGENTS.md"                    # Main project instructions
    - ".cognition/memory/backend.md"  # Backend-specific context
```

Each file is loaded into the system prompt. AGENTS.md is the conventional name for the main project file.

#### Skills

Skills are specialized capabilities defined in SKILL.md files:

```yaml
agent:
  skills:
    - ".cognition/skills/"     # Scan all subdirectories
    - "./shared/skills/"       # Additional skill directories
```

Directory structure:
```
.cognition/skills/
  web-search/
    SKILL.md
  deploy/
    SKILL.md
```

#### Subagents

Define specialized subagents for delegated tasks:

```yaml
agent:
  subagents:
    - name: "security-expert"
      system_prompt: "You are a security expert. Review code for vulnerabilities."
    
    - name: "performance-expert"
      system_prompt: "You are a performance optimization expert."
```

#### Human-in-the-Loop

Require confirmation before executing sensitive tools:

```yaml
agent:
  interrupt_on:
    execute: true          # Confirm before shell commands
    write_file: false      # Auto-approve file writes
    edit_file: false       # Auto-approve file edits
```

### Persistence (`persistence`)

Configure how session state is stored.

```yaml
persistence:
  backend: "sqlite"              # Backend: sqlite, memory, postgres
  uri: ".cognition/state.db"     # Connection URI or file path
```

**Environment Variables:**
- `COGNITION_PERSISTENCE_BACKEND` - Backend type
- `COGNITION_PERSISTENCE_URI` - Connection URI

**Backends:**

- **sqlite** (default): File-based, good for single-server deployments
- **memory**: Non-persistent, sessions lost on restart (testing only)
- **postgres**: Database-backed for multi-server deployments

**Examples:**

**SQLite (default):**
```yaml
persistence:
  backend: "sqlite"
  uri: ".cognition/state.db"
```

**PostgreSQL:**
```yaml
persistence:
  backend: "postgres"
  uri: "postgresql://user:pass@localhost/cognition"
```

### Test Settings (`test`)

Configure testing behavior (development only).

```yaml
test:
  llm_mode: "mock"         # Test LLM mode: mock, openai, ollama
```

**Environment Variable:**
- `COGNITION_TEST_LLM_MODE` - Test LLM mode

Used by the test suite to control which LLM provider tests use.

### Session Scoping (`scoping`)

Configure multi-dimensional session isolation for multi-tenant deployments.

```yaml
scoping:
  enabled: false           # Enable session scoping
  keys: ["user", "project"]  # Scope dimensions (headers: X-Cognition-Scope-User, X-Cognition-Scope-Project)
```

**Environment Variables:**
- `COGNITION_SCOPING_ENABLED` - Enable scoping (default: false)
- `COGNITION_SCOPE_KEYS` - Comma-separated list of scope keys (default: user)

**How it works:**
- Sessions are tagged with scope values extracted from request headers
- `X-Cognition-Scope-{Key}` headers provide scope values
- Session list operations filter by the caller's scope
- Fail-closed: missing required scope headers return 403 when enabled

**Examples:**

**Single-tenant (default):**
```yaml
scoping:
  enabled: false
```
All sessions visible to all callers.

**Multi-user isolation:**
```yaml
scoping:
  enabled: true
  keys: ["user"]
```
Sessions scoped per user via `X-Cognition-Scope-User` header.

**Multi-dimensional (user + project):**
```yaml
scoping:
  enabled: true
  keys: ["user", "project"]
```
Sessions scoped by both user and project. Caller must provide both headers.

**With custom dimensions:**
```yaml
scoping:
  enabled: true
  keys: ["team", "environment"]
```
Uses `X-Cognition-Scope-Team` and `X-Cognition-Scope-Environment` headers.

### Observability Toggles (`observability`)

Control which observability backends are active.

```yaml
observability:
  enabled: true            # Master switch for observability
  otel_enabled: true       # Enable OpenTelemetry tracing
  mlflow_enabled: false    # Enable MLflow tracing
  mlflow_tracking_uri: null  # MLflow tracking server URI
  mlflow_experiment_name: "cognition"  # Default experiment name
```

**Environment Variables:**
- `COGNITION_OBSERVABILITY_ENABLED` - Master observability switch
- `COGNITION_OTEL_ENABLED` - Enable OpenTelemetry (default: true)
- `COGNITION_MLFLOW_ENABLED` - Enable MLflow (default: false)
- `COGNITION_MLFLOW_TRACKING_URI` - MLflow tracking URI
- `COGNITION_MLFLOW_EXPERIMENT_NAME` - MLflow experiment name

**Configuration Matrix:**

| otel_enabled | mlflow_enabled | Result |
|-------------|----------------|---------|
| false | false | Structured logging only |
| true | false | Prometheus + OTel traces |
| false | true | MLflow traces only |
| true | true | Full stack (all backends) |

### Execution Backend (`execution`)

Choose the sandbox environment for code execution.

```yaml
execution:
  backend: "local"         # Backend: local, docker
  docker:
    image: "cognition-sandbox:latest"  # Sandbox image
    network_mode: "none"   # Network isolation: none, bridge
    memory_limit: "512m"   # Memory limit per container
    cpu_limit: 1.0         # CPU cores per container
```

**Environment Variables:**
- `COGNITION_SANDBOX_BACKEND` - Execution backend (default: local)
- `COGNITION_DOCKER_IMAGE` - Docker image for sandbox
- `COGNITION_DOCKER_NETWORK_MODE` - Network isolation mode
- `COGNITION_DOCKER_MEMORY_LIMIT` - Memory limit
- `COGNITION_DOCKER_CPU_LIMIT` - CPU limit

**Backends:**

- **local** (default): Subprocess execution with path containment
- **docker**: Container-per-session with kernel-level isolation

**Docker Mode:**
- Creates a fresh container for each session
- Mounts workspace as read-write volume
- Enforces resource limits and network policies
- Destroys container on session end

### Evaluation (`evaluation`)

Configure MLflow-based evaluation pipeline.

```yaml
evaluation:
  enabled: true            # Enable evaluation tracking
  default_scorers:         # Built-in scorers to run
    - "correctness"
    - "helpfulness"
    - "tool_efficiency"
  custom_scorers:          # Paths to custom scorer modules
    - "./my_scorers/"
```

**Environment Variables:**
- `COGNITION_EVALUATION_ENABLED` - Enable evaluation (default: true)
- `COGNITION_EVALUATION_DEFAULT_SCORERS` - Default scorers to run
- `COGNITION_EVALUATION_CUSTOM_SCORERS` - Custom scorer paths

**Built-in Scorers:**
- `correctness` - Factual accuracy of responses
- `helpfulness` - Actionability and relevance
- `safety` - Policy compliance and toxicity
- `tool_efficiency` - Appropriate tool usage

## Security Best Practices

### API Keys and Secrets

**Never commit secrets to config files.** Use environment variables:

```bash
# .env file (add to .gitignore!)
OPENAI_API_KEY="sk-..."
AWS_ACCESS_KEY_ID="..."
AWS_SECRET_ACCESS_KEY="..."
```

Config files should only contain non-sensitive settings:

```yaml
# .cognition/config.yaml (safe to commit)
llm:
  provider: "openai"
  model: "gpt-4o"
  
server:
  host: "127.0.0.1"
```

### Secret Fields

The following fields are automatically excluded from `config.example.yaml` and should only be set via environment variables:

- `OPENAI_API_KEY` - OpenAI API key
- `COGNITION_OPENAI_COMPATIBLE_API_KEY` - Compatible API key
- `AWS_ACCESS_KEY_ID` - AWS access key
- `AWS_SECRET_ACCESS_KEY` - AWS secret key

## Complete Example Configurations

### Development Setup

```yaml
server:
  host: "127.0.0.1"
  port: 8000
  log_level: "debug"

llm:
  provider: "openai"
  model: "gpt-4o"
  temperature: 0.7

agent:
  memory:
    - "AGENTS.md"
  skills:
    - ".cognition/skills/"
```

With `.env`:
```bash
OPENAI_API_KEY="sk-..."
```

### Production Setup

```yaml
server:
  host: "0.0.0.0"
  port: 8000
  log_level: "warning"
  max_sessions: 500

llm:
  provider: "bedrock"
  model: "anthropic.claude-3-sonnet"

rate_limit:
  per_minute: 120
  burst: 20

observability:
  otel_endpoint: "http://otel-collector:4317"
  metrics_port: 9090

persistence:
  backend: "postgres"
  uri: "postgresql://cognition:${DB_PASSWORD}@db/cognition"

agent:
  interrupt_on:
    execute: true
```

### Local Development with Ollama

```yaml
server:
  host: "127.0.0.1"
  port: 8000

llm:
  provider: "ollama"
  model: "llama3.2"
  
ollama:
  base_url: "http://localhost:11434"

persistence:
  backend: "sqlite"
  uri: ".cognition/state.db"
```

### Multi-Provider Setup

Use different providers for different environments via env vars:

```yaml
llm:
  provider: "${LLM_PROVIDER:-openai}"
  model: "${LLM_MODEL:-gpt-4o}"
```

Then set per-environment:
```bash
# Development
export LLM_PROVIDER="openai"
export OPENAI_API_KEY="sk-..."

# Production  
export LLM_PROVIDER="bedrock"
export AWS_REGION="us-west-2"
```

## Troubleshooting

### Config Not Loading

1. Check file location: `.cognition/config.yaml` (not `config.yaml` in root)
2. Verify YAML syntax: `python -c "import yaml; yaml.safe_load(open('.cognition/config.yaml'))"`
3. Check file permissions: `ls -la .cognition/`

### Environment Variables Not Working

1. Verify prefix: Must use `COGNITION_` (except `OPENAI_API_KEY`, `AWS_*`)
2. Check for typos: Compare with field names in Settings
3. Restart server: Env vars loaded at startup

### Settings Not Applied

Remember precedence: CLI flags > Env vars > Project config > Global config > Defaults

Check effective config:
```bash
cognition config  # CLI command to show merged config
```

## Migration from v1

If upgrading from Cognition v1:

1. Rename `config.yaml` to `.cognition/config.yaml`
2. Update field names to new structure (e.g., `llm.provider` instead of `provider`)
3. Move API keys to `.env` file
4. Remove deprecated fields:
   - `default_model` → use `llm.model`
   - `host`/`port` → use `server.host`/`server.port`

## Reference

### All Configuration Options

| YAML Path | Env Variable | Type | Default | Description |
|-----------|-------------|------|---------|-------------|
| `server.host` | `COGNITION_HOST` | string | `127.0.0.1` | Server bind address |
| `server.port` | `COGNITION_PORT` | int | `8000` | Server port |
| `server.log_level` | `COGNITION_LOG_LEVEL` | string | `info` | Logging level |
| `server.max_sessions` | `COGNITION_MAX_SESSIONS` | int | `100` | Max concurrent sessions |
| `server.session_timeout_seconds` | `COGNITION_SESSION_TIMEOUT_SECONDS` | float | `3600` | Session timeout |
| `workspace.root` | `COGNITION_WORKSPACE_ROOT` | path | `.` | Workspace root |
| `llm.provider` | `COGNITION_LLM_PROVIDER` | string | `mock` | LLM provider |
| `llm.model` | `COGNITION_LLM_MODEL` | string | `gpt-4o` | Model name |
| `llm.temperature` | `COGNITION_LLM_TEMPERATURE` | float | `null` | Temperature |
| `llm.max_tokens` | `COGNITION_LLM_MAX_TOKENS` | int | `null` | Max tokens |
| `llm.system_prompt` | `COGNITION_LLM_SYSTEM_PROMPT` | string | `null` | System prompt |
| `openai.api_base` | `OPENAI_API_BASE` | string | `null` | OpenAI base URL |
| `openai_compatible.base_url` | `COGNITION_OPENAI_COMPATIBLE_BASE_URL` | string | `null` | Compatible API URL |
| `openai_compatible.api_key` | `COGNITION_OPENAI_COMPATIBLE_API_KEY` | secret | `sk-no-key-required` | API key |
| `aws.region` | `AWS_REGION` | string | `us-east-1` | AWS region |
| `aws.access_key_id` | `AWS_ACCESS_KEY_ID` | secret | `null` | AWS access key |
| `aws.secret_access_key` | `AWS_SECRET_ACCESS_KEY` | secret | `null` | AWS secret key |
| `bedrock.model_id` | `COGNITION_BEDROCK_MODEL_ID` | string | `anthropic.claude-3-sonnet...` | Bedrock model |
| `ollama.model` | `COGNITION_OLLAMA_MODEL` | string | `llama3.2` | Ollama model |
| `ollama.base_url` | `COGNITION_OLLAMA_BASE_URL` | string | `http://localhost:11434` | Ollama URL |
| `rate_limit.per_minute` | `COGNITION_RATE_LIMIT_PER_MINUTE` | int | `60` | Rate limit |
| `rate_limit.burst` | `COGNITION_RATE_LIMIT_BURST` | int | `10` | Burst capacity |
| `observability.otel_endpoint` | `COGNITION_OTEL_ENDPOINT` | string | `null` | OTEL endpoint |
| `observability.metrics_port` | `COGNITION_METRICS_PORT` | int | `9090` | Metrics port |
| `agent.memory` | `COGNITION_AGENT_MEMORY` | list | `["AGENTS.md"]` | Memory files |
| `agent.skills` | `COGNITION_AGENT_SKILLS` | list | `[".cognition/skills/"]` | Skill dirs |
| `agent.subagents` | `COGNITION_AGENT_SUBAGENTS` | list | `[]` | Subagents |
| `agent.interrupt_on` | `COGNITION_AGENT_INTERRUPT_ON` | dict | `{}` | Interrupt settings |
| `persistence.backend` | `COGNITION_PERSISTENCE_BACKEND` | string | `sqlite` | Backend type |
| `persistence.uri` | `COGNITION_PERSISTENCE_URI` | string | `.cognition/state.db` | Connection URI |
| `scoping.enabled` | `COGNITION_SCOPING_ENABLED` | bool | `false` | Enable session scoping |
| `scoping.keys` | `COGNITION_SCOPE_KEYS` | list | `["user"]` | Scope dimensions |
| `observability.enabled` | `COGNITION_OBSERVABILITY_ENABLED` | bool | `true` | Observability master switch |
| `observability.otel_enabled` | `COGNITION_OTEL_ENABLED` | bool | `true` | Enable OpenTelemetry |
| `observability.mlflow_enabled` | `COGNITION_MLFLOW_ENABLED` | bool | `false` | Enable MLflow |
| `observability.mlflow_tracking_uri` | `COGNITION_MLFLOW_TRACKING_URI` | string | `null` | MLflow tracking URI |
| `observability.mlflow_experiment_name` | `COGNITION_MLFLOW_EXPERIMENT_NAME` | string | `cognition` | MLflow experiment |
| `execution.backend` | `COGNITION_SANDBOX_BACKEND` | string | `local` | Execution backend |
| `execution.docker.image` | `COGNITION_DOCKER_IMAGE` | string | `cognition-sandbox` | Docker image |
| `execution.docker.network_mode` | `COGNITION_DOCKER_NETWORK_MODE` | string | `none` | Network isolation |
| `execution.docker.memory_limit` | `COGNITION_DOCKER_MEMORY_LIMIT` | string | `512m` | Memory limit |
| `execution.docker.cpu_limit` | `COGNITION_DOCKER_CPU_LIMIT` | float | `1.0` | CPU limit |
| `evaluation.enabled` | `COGNITION_EVALUATION_ENABLED` | bool | `true` | Enable evaluation |
| `test.llm_mode` | `COGNITION_TEST_LLM_MODE` | string | `mock` | Test LLM mode |

**Secret fields** (set via env vars only): `OPENAI_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `COGNITION_OPENAI_COMPATIBLE_API_KEY`
