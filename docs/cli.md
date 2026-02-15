# Cognition CLI

The **Cognition CLI** is a lightweight, terminal-based interface for interacting with the Cognition Agent runtime. It provides a rich TUI experience with real-time streaming, session management, and system telemetry.

## Quick Start

The CLI automatically manages the backend server engine. You don't need to start the server separately.

```bash
# Start an interactive chat session (auto-creates a session)
uv run python -m client.cli.main chat

# Send a single message and exit
uv run python -m client.cli.main chat "Summarize this file" < README.md
```

## Features

- **Auto-Engine Management**: Starts the `cognition-server` in the background if not running.
- **Interactive TUI**: A "Cyberpunk" themed shell with history, markdown rendering, and syntax highlighting.
- **Real-time Telemetry**: Tracks token usage, cost estimates, and model information.
- **Session Persistence**: Automatically resumes the last active session.
- **Piped Input**: Supports piping file contents or command output directly to the agent.

## Commands

### Chat

The primary interface for interacting with the agent.

```bash
# Interactive mode (default)
uv run python -m client.cli.main chat

# Start chat in a specific session
uv run python -m client.cli.main chat --session <session_id>

# Single message mode (non-interactive)
uv run python -m client.cli.main chat "What is the status of the server?" --single
```

### Session Management

Manage your conversation history and contexts.

```bash
# List all sessions
uv run python -m client.cli.main session list

# Create a new named session
uv run python -m client.cli.main session create --title "Project Alpha Debugging"
```

### Engine Control

Control the background server process.

```bash
# Check engine status
uv run python -m client.cli.main status

# Stop the background engine
uv run python -m client.cli.main stop
```

## Interactive Shell Reference

When in interactive mode (`chat`), the following slash commands are available:

| Command | Description |
| :--- | :--- |
| `/help` | Show available commands |
| `/clear` | Clear the screen buffer |
| `/model <id>` | Switch the active LLM (e.g., `/model gpt-4o`) |
| `/session` | Display current session ID |
| `/status` | Check connection status to the engine |
| `/exit` | Exit the shell |

## Configuration

The CLI stores state and logs in `~/.cognition/`:
- `logs/engine.log`: Server output and debug logs.
- `.cognition-cli-state.json`: Last active session ID.
- `history`: Command history for the interactive shell.

Environment variables:
- `COGNITION_SERVER_URL`: Override the default server URL (default: `http://localhost:8000`).
