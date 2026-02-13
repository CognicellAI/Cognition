# Guide: Extending the Engine (Custom Tools)

> **Teach your Agent new skills.**

Out of the box, Cognition knows how to read files and run shell commands. But to build a specialized platform (like GeneSmith or ZeroOne), you need to give it domain-specific tools.

This guide shows you how to add a custom tool to the engine.

## 1. Define the Tool

A tool is just a Python function with type hints and a docstring. The Agent uses the docstring to understand *when* and *how* to use the tool.

Create a new file `server/app/agent/custom_tools.py`:

```python
from langchain_core.tools import tool

@tool
def fetch_stock_price(symbol: str) -> str:
    """Fetch the current price of a stock ticker.
    
    Args:
        symbol: The stock symbol (e.g., 'AAPL', 'BTC-USD')
    """
    # In a real app, you would call an external API here
    # import requests
    # resp = requests.get(f"https://api.example.com/price/{symbol}")
    # return resp.json()["price"]
    
    return f"The price of {symbol} is $150.00 (Mock Data)"
```

## 2. Register the Tool

You need to tell the Agent Factory to include this tool when creating new agents.

Edit `server/app/agent/cognition_agent.py`:

```python
# ... imports
from server.app.agent.custom_tools import fetch_stock_price

def create_cognition_agent(...):
    # ... existing setup ...
    
    # Add your custom tool to the list
    tools = [fetch_stock_price] 
    
    # Create the agent with tools
    agent = create_deep_agent(
        model=model,
        system_prompt=prompt,
        backend=backend,
        checkpointer=checkpointer,
        tools=tools,  # <--- Pass tools here
    )
    
    return agent
```

## 3. Rebuild the Engine

Since you modified the server code, you must rebuild the Docker image.

```bash
docker-compose up -d --build
```

## 4. Verify

Start a session and ask the agent to use the new tool.

```bash
curl -X POST http://localhost:8000/sessions ... # Create session
curl -X POST http://localhost:8000/sessions/$ID/messages \
  -d '{"content": "What is the price of AAPL?"}'
```

**Result:**
The agent will call `fetch_stock_price("AAPL")` and reply: *"The price of AAPL is $150.00."*

## Advanced: Tools with Sandbox Access

Sometimes a tool needs to access the filesystem or run commands (e.g., a "Lint" tool that runs `flake8`).

You can inject the `backend` into your tool class.

```python
class LintTool:
    def __init__(self, backend):
        self.backend = backend

    def run(self, path: str):
        # Use the sandbox to run the command safely
        return self.backend.execute(f"flake8 {path}")
```

## Best Practices

1.  **Type Hints:** Always type hint arguments. The LLM uses this to generate the JSON schema.
2.  **Docstrings:** Write verbose docstrings. Explain edge cases and return formats.
3.  **Error Handling:** If your tool fails (e.g., API down), raise a descriptive error. The Agent serves as the error handler and will try to fix it.
