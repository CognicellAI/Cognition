# Blueprint: Cognition CLI

This blueprint showcases how to build a high-fidelity, interactive terminal client on top of the Cognition Agent runtime. It serves as a reference implementation for developers wanting to integrate Cognition into their existing terminal workflows or build custom CLI tools.

## Concept

The CLI acts as a "thin client" that communicates with the Cognition Server via HTTP and Server-Sent Events (SSE). It prioritizes a low-latency, "always-on" feel by handling server lifecycle management automatically.

## Architecture

The CLI follows a modular design:
1.  **Orchestrator (`main.py`)**: Handles command routing (Typer), engine management, and session state.
2.  **Shell (`shell.py`)**: A rich REPL built with `prompt_toolkit` and `rich`.
3.  **SSE Parser**: A robust parser for handling multi-line data and paired event/data lines in a streaming response.

## Key Implementation Patterns

### 1. Automatic Engine Management
The CLI ensures the server is running before attempting to communicate. If the server is not found, it spawns a background process and waits for a healthy `/ready` signal.

```python
# From client/cli/main.py
def ensure_engine():
    # ... check if running ...
    subprocess.Popen(
        [sys.executable, "-m", "server.app.cli", "serve"],
        stdout=f, stderr=f, start_new_session=True
    )
    # ... wait for /ready ...
```

### 2. High-Fidelity Streaming
To provide a smooth reading experience, the CLI uses the `Live` display from the `rich` library. This allows updating the rendered Markdown in place as tokens arrive.

```python
with Live(Markdown(""), refresh_per_second=15) as live:
    async for event in response.aiter_lines():
        # ... parse and update accumulated_text ...
        live.update(Markdown(accumulated_text))
```

### 3. Real-Time Telemetry (HUD)
The interactive shell includes a "Heads-Up Display" (HUD) that shows model information and token usage after every response, keeping the user informed of the session cost and context.

## Extension Ideas

Developers can build on this blueprint to:
- **Add IDE Integration**: Build plugins for VS Code or JetBrains that use the same session API.
- **Custom Tool Hooks**: Add CLI-side tools that can be called by the agent.
- **Project Indexing**: Integrate a local vector database to provide the agent with project-wide context before starting a session.
- **Multi-Agent Orchestration**: Use the CLI to coordinate between multiple specialized Cognition agents.

## Implementation Details

Refer to the source code for the full implementation:
- [client/cli/main.py](../../client/cli/main.py)
- [client/cli/shell.py](../../client/cli/shell.py)
