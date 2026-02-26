# Guide: Creating and Managing Custom Tools

> **Teach your Agent new skills with automatic discovery and hot-reloading.**

## Overview

The Tool Registry provides automatic discovery and management of custom tools. Simply place Python files with `@tool` decorated functions in `.cognition/tools/`, and they'll be automatically discovered, registered, and made available to your agents.

## Quick Start

### 1. Create a Tool

Place a Python file in `.cognition/tools/`:

```bash
cognition create tool calculator
```

Or create manually:

```python
# .cognition/tools/calculator.py
from langchain_core.tools import tool

@tool
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression safely.
    
    Args:
        expression: A mathematical expression like "2 + 2" or "sqrt(16)"
        
    Returns:
        The result of the calculation
    """
    try:
        # Safe evaluation using Python's eval with limited scope
        allowed_names = {
            "abs": abs, "round": round, "max": max, "min": min,
            "sum": sum, "pow": pow, "len": len,
        }
        result = eval(expression, {"__builtins__": {}}, allowed_names)
        return f"Result: {result}"
    except Exception as e:
        return f"Error: {str(e)}"
```

### 2. Verify Registration

Check that your tool was discovered:

```bash
cognition tools list
```

Output:
```
Registered Tools:
┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Name       ┃ Source                              ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ calculator │ .cognition/tools/calculator.py     │
└────────────┴─────────────────────────────────────┘
Total: 1 tool(s)
```

### 3. Use in Conversation

The tool is automatically available to all agents:

```
User: What is 25 * 4?

Agent: I'll calculate that for you.
[Tool Call: calculator(expression="25 * 4")]

Result: 100
```

## Tool Development Workflow

### Create

```bash
# Create a new tool
cognition create tool my_custom_tool

# With specific directory
cognition create tool my_tool --path ./custom_tools/
```

This creates a template file with:
- Proper `@tool` decorator
- Type hints and docstrings
- Error handling
- Security-safe patterns

### Edit

Edit the generated file:

```python
# .cognition/tools/my_custom_tool.py
from langchain_core.tools import tool

@tool
def my_custom_tool(query: str) -> str:
    """Description of what this tool does.
    
    Args:
        query: Description of the parameter
        
    Returns:
        Description of the return value
    """
    # Your implementation here
    return f"Processed: {query}"
```

### Reload

After editing, reload to pick up changes:

```bash
cognition tools reload
```

Or use the API:

```bash
curl -X POST http://localhost:8000/tools/reload
```

### Verify

Check for errors:

```bash
cognition tools list
# Or via API:
curl http://localhost:8000/tools/errors
```

## Tool Registry Features

### Automatic Discovery

The registry automatically discovers tools from:

- `.cognition/tools/` (default)
- Configured `tools_path` in settings
- Subdirectories (recursive)

### Hot Reloading

Changes are picked up automatically:

```mermaid
graph LR
    A[Edit Tool] --> B[Save File]
    B --> C[File Watcher Detects]
    C --> D[Registry Reloads]
    D --> E[Agent Uses New Version]
```

### Security Scanning

All tools are scanned before loading:

```python
# These imports will be flagged:
import os          # ❌ Blocked
import subprocess  # ❌ Blocked
import socket      # ❌ Blocked
```

**Security Levels:**
- `warn` (default): Log violations, continue loading
- `strict`: Block tool load, emit error

### Error Visibility

If a tool fails to load, errors are visible:

**CLI:**
```bash
cognition tools list
# Shows errors at bottom of output
```

**API:**
```bash
curl http://localhost:8000/tools/errors
```

**SSE Stream:**
Errors are emitted as events for real-time UI updates.

## Tool Configuration

### Agent-Level Configuration

```yaml
# .cognition/config.yaml
agent:
  tools:
    - "server.app.tools.file_reader"
    - ".cognition.tools.my_custom_tool"
  
  # Tool blocklist
  tool_blocklist:
    - "execute_command"
    
  # Middleware
  middleware:
    - name: tool_retry
      max_retries: 3
    - name: pii
      pii_types: [email, phone]
```

### Middleware

Available upstream middleware:

| Middleware | Purpose | Example Config |
|------------|---------|----------------|
| `tool_retry` | Retry failed tools | `max_retries: 3, backoff_factor: 2.0` |
| `tool_call_limit` | Limit calls per run | `run_limit: 50` |
| `pii` | Redact PII | `pii_types: [email, phone]` |
| `human_in_the_loop` | Require approval | `approve_tools: [delete_file]` |

## Advanced Topics

### Programmatic Registration

For built-in tools:

```python
from server.app.agent_registry import get_agent_registry

registry = get_agent_registry()
registry.register_tool(
    name="my_api_client",
    factory=lambda: create_api_client(),
    source="programmatic"
)
```

### Custom Tool Class

```python
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

class SearchInput(BaseModel):
    query: str = Field(description="Search query")
    limit: int = Field(default=10, description="Max results")

class SearchTool(BaseTool):
    name: str = "web_search"
    description: str = "Search the web"
    args_schema: type[BaseModel] = SearchInput
    
    def _run(self, query: str, limit: int = 10) -> str:
        # Implementation
        return f"Results for: {query}"
```

### Tool Testing

```python
# test_my_tool.py
from .cognition.tools.calculator import calculator

def test_calculator():
    result = calculator.invoke({"expression": "2 + 2"})
    assert "4" in result
```

## Best Practices

### 1. Clear Docstrings

The agent uses docstrings to decide when to use tools:

```python
@tool
def analyze_csv(file_path: str) -> str:
    """Analyze a CSV file and return statistics.
    
    Use this when the user wants to:
    - Get summary statistics
    - Find correlations
    - Create visualizations
    
    Args:
        file_path: Path to the CSV file relative to workspace
        
    Returns:
        JSON string with statistics
    """
    pass
```

### 2. Type Hints

Always use type hints for better agent understanding:

```python
@tool
def fetch_data(url: str, timeout: int = 30) -> dict[str, Any]:
    """..."""
    pass
```

### 3. Error Handling

Tools should return user-friendly errors:

```python
@tool
def read_file(path: str) -> str:
    """Read a file."""
    try:
        return Path(path).read_text()
    except FileNotFoundError:
        return f"Error: File '{path}' not found"
    except PermissionError:
        return f"Error: Permission denied for '{path}'"
```

### 4. Security

Never use dangerous imports:

```python
# ❌ BAD
import os
os.system(command)

# ❌ BAD
import subprocess
subprocess.run(command, shell=True)

# ✅ GOOD
# Use allowed operations only
```

## Troubleshooting

### Tool Not Appearing

1. Check file is in `.cognition/tools/`
2. Verify `@tool` decorator is present
3. Run `cognition tools reload`
4. Check errors: `curl http://localhost:8000/tools/errors`

### Import Errors

```bash
# Check syntax
python -m py_compile .cognition/tools/my_tool.py
```

### Security Violations

```bash
# See security errors
curl http://localhost:8000/tools/errors
```

Example response:
```json
[
  {
    "file": "/workspace/.cognition/tools/bad_tool.py",
    "error_type": "SecurityError",
    "error": "Banned import: os",
    "timestamp": 1708963200.0
  }
]
```

## API Reference

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/tools` | GET | List all tools |
| `/tools/{name}` | GET | Get tool details |
| `/tools/errors` | GET | Get load errors |
| `/tools/reload` | POST | Trigger reload |

### CLI Commands

| Command | Description |
|---------|-------------|
| `cognition tools list` | List tools |
| `cognition tools reload` | Reload tools |
| `cognition create tool <name>` | Create tool |

## Related Documentation

- [Tool Registry Design](../concepts/tool-registry-design.md) - Architecture details
- [Security Hardening](../concepts/security.md) - Security model
- [Extending Agents](./extending-agents.md) - Skills and memory
- [P3-TR Implementation](../../ROADMAP.md#p3-tr--tool-registry-end-to-end) - Roadmap
