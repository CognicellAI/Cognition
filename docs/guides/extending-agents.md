# Guide: Extending the Agent

> **Customize your Agent's behavior without editing core code.**

Cognition follows a "Convention-over-Configuration" model. For most customizations, you don't need to write Python code—you just need to add files or update your configuration.

## 1. Add Memory (Project Rules)

The fastest way to customize an agent is to provide it with **Memory**. Cognition automatically looks for a file named `AGENTS.md` in the root of your workspace.

1.  Create `AGENTS.md` in your project root.
2.  Add rules, naming conventions, or project context.

```markdown
# AGENTS.md - Project Conventions

- We use 'snake_case' for all variable names.
- Always add unit tests for new utility functions.
- The production database is located at /mnt/data/prod.db.
```

**Result:** The Agent will load this file into its system prompt automatically at the start of every session.

---

## 2. Add Skills (Reusable Workflows)

Skills are reusable "Action Templates" stored in `.cognition/skills/`. They use **Progressive Disclosure**, meaning they don't consume tokens until the agent needs them.

1.  Create a directory: `.cognition/skills/my-skill/`
2.  Create a file named `SKILL.md` inside it.
3.  Add YAML frontmatter and instructions.

```markdown
---
name: security-audit
description: Run a comprehensive security audit on the codebase.
---

To run a security audit:
1. Run 'bandit -r .' to find common security issues.
2. Check for hardcoded secrets using 'trufflehog'.
3. Summarize the high-risk findings.
```

**Result:** The Agent will see "security-audit" in its tool list. When invoked, it will read the full instructions.

---

## 3. Define Custom Agents

Cognition supports a **Multi-Agent Registry** that allows you to define both primary agents (for session creation) and specialized subagents (for delegation via the `task` tool).

### Built-in Agents

Cognition ships with built-in agents available out of the box:

| Agent | Mode | Description |
|-------|------|-------------|
| `default` | primary | Full-access coding agent with all tools enabled |
| `readonly` | primary | Analysis-only agent with write/execute tools disabled |

### Creating Custom Agents

Create agent definition files in `.cognition/agents/`:

**Markdown format** (`.cognition/agents/researcher.md`):
```markdown
---
name: researcher
description: A specialized research agent for gathering information
mode: subagent
---

You are a specialized research agent focused on gathering and synthesizing information.
You should search for relevant information, analyze sources, and provide comprehensive summaries.
```

**YAML format** (`.cognition/agents/security-auditor.yaml`):
```yaml
name: security-auditor
description: Security-focused code reviewer
mode: subagent
system_prompt: |
  You are a security auditor. Focus on identifying vulnerabilities,
  unsafe patterns, and compliance issues in the code.
```

**Agent Modes:**
- `primary` — Can be used to create sessions (e.g., `POST /sessions` with `agent_name`)
- `subagent` — Can only be invoked by other agents via the `task` tool
- `all` — Can function as both primary and subagent

### Using Custom Agents

**For sessions:** Create a session with a specific primary agent:
```bash
curl -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"agent_name": "readonly", "title": "Code Review"}'
```

**For subagent delegation:** All `subagent` and `all` mode agents are automatically available to primary agents via Deep Agents' native `task` tool. Your primary agent can say: *"I'll use the researcher subagent to gather more information."*

### Agent API

Query available agents via the API:

```bash
# List all non-hidden agents
curl http://localhost:8000/agents

# Get specific agent details
curl http://localhost:8000/agents/readonly
```

---

## 4. Enable Human-in-the-Loop (Guardrails)

You can require human approval for dangerous tools like `execute` or `write_file`.

Edit `.cognition/config.yaml`:

```yaml
agent:
  interrupt_on:
    execute: true  # Pause and ask before running any shell command
```

---

## 5. Custom Middleware (Advanced Hooks)

If you need to add custom logging, metrics, or complex logic hooks, you can write a **Custom Middleware**.

1.  Create `server/app/agent/my_middleware.py`:

```python
from langchain.agents.middleware.types import AgentMiddleware, wrap_tool_call

class AuditMiddleware(AgentMiddleware):
    name = "audit_logger"

    @wrap_tool_call()
    async def awrap_tool_call(self, request, handler):
        # Log the tool call before it happens
        print(f"Agent is calling: {request.tool_call['name']}")
        return await handler(request)
```

2.  Register it in `server/app/agent/cognition_agent.py`.

---

## 6. Register a Custom LLM Provider

Cognition uses a **Provider Registry** to instantiate LLM models.

1.  Create your factory function:

```python
def create_my_custom_llm(config, settings):
    # Return a LangChain ChatModel instance
    from my_provider import MyChatModel
    return MyChatModel(api_key=config.api_key)
```

2.  Register it in `server/app/llm/registry.py`:

```python
from server.app.llm.registry import register_provider
register_provider("my-custom-provider", create_my_custom_llm)
```

3.  Use it in config: `COGNITION_LLM_PROVIDER=my-custom-provider`
