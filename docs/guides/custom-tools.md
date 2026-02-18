# Guide: Adding Custom Tools

> **Teach your Agent new skills.**

Out of the box, Cognition knows how to read files and run shell commands. But to build a specialized platform, you need to give it domain-specific tools.

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
    return f"The price of {symbol} is $150.00 (Mock Data)"
```

## 2. Register the Tool

You need to tell the Agent Factory to include this tool when creating new agents.

Edit `server/app/agent/cognition_agent.py`:

```python
from server.app.agent.custom_tools import fetch_stock_price

def create_cognition_agent(
    # ...
    tools: list[Any] | None = None,
):
    # ...
    agent_tools = list(tools) if tools else []
    agent_tools.append(fetch_stock_price)
    
    agent = create_deep_agent(
        # ...
        tools=agent_tools,
    )
```

## 3. Verify

Rebuild and start a session:

```bash
docker-compose up -d --build
```

Ask the agent: *"What is the price of AAPL?"*

---

## Alternative: Skills (Zero-Code)

If your tool is a multi-step workflow (e.g., "Run a security audit"), you can use **Skills** instead of writing Python code.

Refer to the **[Extending Agents Guide](./extending-agents.md)** for more information on:
- **Memory:** Adding project-specific rules via `AGENTS.md`.
- **Skills:** Defining reusable workflows in `.cognition/skills/`.
- **Subagents:** Orchestrating specialized experts.
