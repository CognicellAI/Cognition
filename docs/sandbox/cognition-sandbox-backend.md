# Cognition Sandbox Backend

## Overview

The **Cognition Sandbox Backend** is a custom implementation of the `SandboxBackendProtocol` from the `deepagents` library. It provides isolated command execution and file operations for AI agents, enabling safe multi-step task completion in a local environment.

## What It Does

The sandbox backend enables AI agents to:

1. **Execute Shell Commands** - Run commands in an isolated workspace (tests, git, etc.)
2. **Manipulate Files** - Read, write, edit files within the workspace
3. **Search Files** - Use glob patterns and grep to find files and content
4. **Work in Isolation** - All operations are contained within a specific root directory

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Deep Agents Agent                       │
│                                                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              CognitionSandboxBackend                   │  │
│  │  ┌─────────────────────────────────────────────┐      │  │
│  │  │           LocalSandbox                       │      │  │
│  │  │  - subprocess execution                      │      │  │
│  │  │  - chroot-like containment                   │      │  │
│  │  │  - timeout handling                          │      │  │
│  │  └─────────────────────────────────────────────┘      │  │
│  │                                                          │  │
│  │  Implements SandboxBackendProtocol:                     │  │
│  │  • execute()   - Shell command execution                │  │
│  │  • ls_info()   - List directory contents                │  │
│  │  • read()      - Read file with line numbers            │  │
│  │  • write()     - Create or overwrite files              │  │
│  │  • edit()      - Find and replace in files              │  │
│  │  • glob_info() - Find files by pattern                  │  │
│  │  • grep_raw()  - Search file contents                   │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Key Components

### 1. LocalSandbox (Core Execution Engine)

The `LocalSandbox` class wraps Python's `subprocess` module to execute commands:

```python
class LocalSandbox:
    def __init__(self, root_dir: str | Path)
    def execute(command: str, timeout=300.0) -> ExecuteResult
```

**Features:**
- Commands execute with `cwd` set to the workspace root
- Combines stdout/stderr for unified output
- Handles timeouts (default: 5 minutes)
- Returns structured results with exit code

### 2. CognitionSandboxBackend (Protocol Implementation)

Implements `SandboxBackendProtocol` from `deepagents`:

```python
class CognitionSandboxBackend(SandboxBackendProtocol):
    def __init__(self, root_dir: str | Path, sandbox_id: Optional[str] = None)
```

When used with `create_deep_agent()`, the agent automatically gains access to an `execute` tool.

## File Operations

### Listing Directory Contents

```python
backend.ls_info(path="/") -> list[FileInfo]
```

Uses `ls -la` to list files and returns structured `FileInfo` objects.

**Example:**
```python
files = backend.ls_info("/app")
# Returns: [
#   FileInfo(path='/app/server', is_dir=True, size=0),
#   FileInfo(path='/app/hello.py', is_dir=False, size=1024)
# ]
```

### Reading Files

```python
backend.read(file_path: str, offset=0, limit=2000) -> str
```

Returns file content with line numbers using `cat -n`.

**Features:**
- Line numbers for precise referencing
- Offset and limit for large files
- Error handling for missing files

### Writing Files

```python
backend.write(file_path: str, content: str) -> WriteResult
```

Creates or overwrites files using `printf` for safe content handling.

**Example:**
```python
result = backend.write("/app/script.py", 'print("Hello")')
# WriteResult(error=None, path='/app/script.py', files_update={'written': True})
```

### Editing Files

```python
backend.edit(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all=False
) -> EditResult
```

Performs find-and-replace operations on files.

**Features:**
- Replace first occurrence or all occurrences
- Tracks how many replacements were made
- Returns file update metadata

### Searching Files

**Glob pattern matching:**
```python
backend.glob_info(pattern="*.py", path="/app") -> list[FileInfo]
```

**Grep content search:**
```python
backend.grep_raw(pattern="def main", path="/app/src") -> list[GrepMatch]
```

## Command Execution

### The `execute` Tool

When you create an agent with the sandbox backend, the agent automatically gets an `execute` tool:

```python
from deepagents import create_deep_agent
from server.app.agent import CognitionSandboxBackend

backend = CognitionSandboxBackend(root_dir="/workspace")
agent = create_deep_agent(backend=backend)

# The agent can now use:
# - execute("pytest tests/")
# - execute("git status")
# - execute("ls -la")
```

### ExecuteResponse

```python
ExecuteResponse(
    output: str,      # Combined stdout/stderr
    exit_code: int,   # Command exit code
    truncated: bool   # True if output was >100KB
)
```

**Features:**
- Output is truncated at ~100KB to prevent memory issues
- Exit code preserved for error handling
- Combined stdout/stderr for complete visibility

## Local Environment Setup

### Docker Compose Configuration

```yaml
services:
  cognition:
    build:
      context: .
      dockerfile: Dockerfile
    volumes:
      - ./workspaces:/workspace  # Mount workspace directory
    environment:
      - COGNITION_WORKSPACE_ROOT=/workspace
```

### Workspace Structure

```
/workspace/              # Root sandbox directory
├── project1/           # Project-specific workspaces
│   ├── src/
│   ├── tests/
│   └── README.md
└── project2/
    └── ...
```

### Creating an Agent

```python
from server.app.agent import create_cognition_agent

# Create agent with sandbox
agent = create_cognition_agent(
    project_path="/workspace/my-project",
    model=my_llm_model,
)

# The agent now has access to:
# - File operations (read, write, edit, ls, glob, grep)
# - Command execution (execute tool)
# - Automatic multi-step ReAct loop
```

## Multi-Step Task Completion

### How It Works

1. **User Request**: "Create a Python script and run it"
2. **Agent Planning**: Uses `write_todos` to create a plan
3. **Tool Execution**:
   - Calls `write_file` to create `script.py`
   - Calls `execute("python script.py")` to run it
4. **Result Streaming**: Each step streams back via SSE
5. **Completion**: Agent returns final response

### Example Flow

```
User: Create a Hello World script and run it

Agent (Tool Call): write_file("/workspace/hello.py", 'print("Hello World")')
Backend: WriteResult(error=None, path="/workspace/hello.py", ...)

Agent (Tool Call): execute("python /workspace/hello.py")
Backend: ExecuteResponse(output="Hello World\n", exit_code=0, ...)

Agent (Final Response): I've created and run the script. Output: Hello World
```

## Security Considerations

### Isolation

- All commands execute within the specified `root_dir`
- No chroot or container isolation (LocalSandbox uses subprocess)
- For stronger isolation, use containerized sandboxes (Modal, Runloop, Daytona)

### Permissions

- Container runs as non-root user (UID 1000)
- Workspace directory owned by the container user
- File operations respect Unix permissions

### Resource Limits

- Command timeout: 5 minutes default
- Output truncation: 100KB limit
- No built-in CPU/memory limits (handled by Docker Compose)

## Debugging

### Viewing Tool Calls

Check the SSE stream for tool execution events:

```javascript
// Event: tool_call
{
  "name": "execute",
  "args": {"command": "ls -la"},
  "id": "abc123"
}

// Event: tool_result
{
  "tool_call_id": "abc123",
  "output": "total 12\ndrwxr-xr-x ...",
  "exit_code": 0
}
```

### Logs

```bash
# View container logs
docker-compose logs -f cognition

# Check for tool execution errors
```

## Comparison with Other Backends

| Feature | StateBackend | FilesystemBackend | CognitionSandboxBackend |
|---------|-------------|------------------|------------------------|
| Persistence | Ephemeral (session) | Disk | Disk + Command Execution |
| execute() tool | ❌ | ❌ | ✅ |
| Use case | Stateless agents | File manipulation | Full dev environment |
| Container | N/A | N/A | Docker |

## Troubleshooting

### "Permission denied" errors

Ensure the workspace directory is writable by the container user:
```bash
docker-compose exec cognition ls -la /workspace
```

### "Command not found"

Verify tools are installed in the container:
```bash
docker-compose exec cognition which python git
```

### Large output truncation

The backend truncates output at ~100KB. For large files, use offset/limit:
```python
backend.read("/app/large.log", offset=1000, limit=100)
```

## Future Enhancements

1. **Container Sandboxes**: Integrate with Modal, Runloop, or Daytona for stronger isolation
2. **Resource Limits**: Add CPU/memory constraints per command
3. **Network Isolation**: Toggle network access for commands
4. **Audit Logging**: Log all commands for security review

## Related Documentation

- [DeepAgents Documentation](https://docs.langchain.com/langsmith/trace-deep-agents)
- [LangGraph Checkpointing](https://langchain-ai.github.io/langgraph/concepts/persistence/)
- [Docker Compose Setup](../docker-compose.md)
- [Agent Architecture](../architecture.md)
