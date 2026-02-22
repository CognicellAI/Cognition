# Agent Extension Points

Cognition is designed to be extensible. You can add capabilities in six ways.

## 1. Memory (AGENTS.md)

Place an `AGENTS.md` file in the project root. It is automatically injected into the system prompt.
Use this for project-specific conventions, style guides, and critical rules.

## 2. Skills (SKILL.md)

Skills are modular instruction sets stored in `.cognition/skills/`.
Structure:

```
.cognition/skills/
  deploy-app/
    SKILL.md
    references/
    scripts/
```

The agent sees the `name` and `description` in `SKILL.md` and loads the full content only when relevant.

## 3. Custom Tools

You can register Python functions as tools.

**Programmatic:**
```python
def my_tool(arg: str) -> str:
    """Description of what the tool does."""
    return f"Processed {arg}"

agent = create_cognition_agent(tools=[my_tool])
```

**Configuration (YAML):**
Tools must be importable in the Python path.
```yaml
tools:
  - "myproject.tools.analysis.run_analysis"
```

## 4. Subagents

Define specialized agents for complex sub-tasks.
Subagents have their own system prompt and toolset.

```yaml
subagents:
  - name: security-auditor
    system_prompt: "You are a security expert. Audit code for vulnerabilities."
    tools:
      - "myproject.tools.security.scan"
```

## 5. Middleware

Intercept and modify agent behavior using LangChain middleware.
Useful for logging, policy enforcement, or side effects.

```python
class AuditMiddleware(AgentMiddleware):
    async def awrap_model_call(self, request, handler):
        print(f"Calling model: {request.model}")
        return await handler(request)
```

Register via `middleware` in `create_cognition_agent` or YAML.

## 6. Custom LLM Providers

Register new LLM providers if the built-in ones (OpenAI, Bedrock, Ollama) aren't enough.

```python
from server.app.llm.registry import register_provider

def create_custom_llm(config, settings):
    return MyCustomLLM(...)

register_provider("custom", create_custom_llm)
```

## Testing Extensions

Testing custom tools and subagents is critical. Use `pytest` and `unittest.mock`.

### Unit Testing Tools

```python
# tests/test_my_tool.py
from myproject.tools.analysis import run_analysis

def test_run_analysis_success():
    result = run_analysis("test_file.py")
    assert "Found 0 issues" in result

def test_run_analysis_failure():
    # Mock external dependency
    with unittest.mock.patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        result = run_analysis("bad_file.py")
        assert "Error running analysis" in result
```

### Integration Testing Agents

Use the `AgentRuntime` in `mock` mode to test full agent flows without spending tokens.

```python
# tests/test_agent_flow.py
import pytest
from server.app.agent.runtime import create_agent_runtime
from server.app.agent.definition import load_agent_definition

@pytest.mark.asyncio
async def test_agent_process():
    # Load agent definition
    definition = load_agent_definition(".cognition/agent.yaml")
    
    # Create runtime with mock provider
    runtime = await create_agent_runtime(
        definition=definition,
        workspace_path="/tmp/test_workspace",
        thread_id="test-session-1",
        provider="mock"  # Force mock mode
    )
    
    # Run a turn
    result = await runtime.ainvoke("Analyze this code")
    
    # Verify tool calls were made
    assert "run_analysis" in [call.name for call in result.tool_calls]
```
