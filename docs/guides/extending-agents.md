# Extending Agents

Cognition uses a convention-over-configuration model. Most extensions require zero code changes — drop a file in the right directory and the server picks it up automatically (via the file watcher). More powerful extensions require Python.

| Level | Mechanism | Code Required | Hot-Reload |
|---|---|---|---|
| Memory | `AGENTS.md` | No | Yes |
| Skills | `.cognition/skills/` SKILL.md files | No | Yes |
| Agents | `.cognition/agents/` YAML or Markdown | No | Yes |
| Tools | Python functions | Yes | Yes |
| Middleware | Python classes | Yes | No |
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

### Directory Structure

```
.cognition/skills/
  deploy-app/
    SKILL.md           # instructions for deploying the application
    references/        # optional supporting files
      checklist.md
  run-migrations/
    SKILL.md
```

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

Configure skill directories:

```yaml
agent:
  skills:
    - ".cognition/skills/"
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
  - "myapp.tools.security.run_semgrep"
config:
  model: gpt-4o
  temperature: 0.1
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
  - "myapp.tools.data.load_csv"
  - "myapp.tools.data.plot_chart"
config:
  model: gpt-4o
  temperature: 0.2
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

---

## 4. Custom Tools

Tools are Python callables that the agent can invoke. Cognition converts them to LangChain tools automatically.

### Simple Function Tool

```python
# myapp/tools/analysis.py
import subprocess

def run_linter(file_path: str) -> str:
    """Run ruff linter on a Python file and return the findings.

    Args:
        file_path: Path to the Python file to lint.

    Returns:
        Linter output as a string.
    """
    result = subprocess.run(
        ["ruff", "check", file_path],
        capture_output=True,
        text=True,
    )
    return result.stdout or "No issues found."
```

The docstring becomes the tool description shown to the agent. Type annotations become the argument schema.

### Register via Config

```yaml
# .cognition/config.yaml
agent:
  tools:
    - "myapp.tools.analysis.run_linter"
    - "myapp.tools.data.query_database"
```

### Auto-Discovery

Drop Python files into `.cognition/tools/` and they are discovered automatically. Each public function in the file becomes a tool. The file watcher reloads them on change.

```python
# .cognition/tools/my_tools.py

def fetch_ticket(ticket_id: str) -> str:
    """Fetch a Jira ticket by ID and return its summary and status."""
    ...

def post_comment(ticket_id: str, comment: str) -> str:
    """Post a comment to a Jira ticket."""
    ...
```

### Async Tools

Async functions are supported natively:

```python
async def call_api(endpoint: str, payload: dict) -> str:
    """Call an internal API endpoint with a JSON payload."""
    async with httpx.AsyncClient() as client:
        response = await client.post(endpoint, json=payload)
        return response.text
```

### Programmatic Registration

```python
from server.app.agent.cognition_agent import create_cognition_agent
from server.app.agent.definition import AgentDefinition

definition = AgentDefinition(
    name="my-agent",
    system_prompt="You are a helpful assistant.",
    tools=["myapp.tools.analysis.run_linter"],
)

agent = await create_cognition_agent(definition, settings)
```

### Testing Tools

```python
# tests/unit/test_my_tools.py
from myapp.tools.analysis import run_linter
from unittest.mock import patch, MagicMock

def test_run_linter_clean_file():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = run_linter("clean.py")
    assert result == "No issues found."

def test_run_linter_with_issues():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout="clean.py:1:1: E501 Line too long", returncode=1
        )
        result = run_linter("messy.py")
    assert "E501" in result
```

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

Connect to any remote Model Context Protocol (MCP) server. MCP servers expose tools over HTTP SSE.

```yaml
# .cognition/config.yaml
mcp:
  servers:
    - name: github-tools
      url: https://mcp.github.example.com/sse
    - name: internal-db
      url: http://db-tools.internal:8080/sse
```

All tools exposed by the MCP server become available to the agent under the server name as a namespace prefix (e.g. `github-tools/create_pr`).

Only HTTP/HTTPS URLs are accepted — stdio-based MCP servers are not supported for security reasons.

---

## 7. Custom LLM Providers

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

The file watcher (`server/app/file_watcher.py`) monitors `.cognition/tools/`, `.cognition/middleware/`, and `.cognition/agents/` using `watchdog`. When any file in these directories changes:

1. Tool registry is reloaded (new tools available, removed tools gone)
2. Agent definition registry is reloaded (new/updated agents loaded)
3. Agent cache is invalidated so the next session uses the updated definition

No server restart required. Changes typically take effect within 1 second.
