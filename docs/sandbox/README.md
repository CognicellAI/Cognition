# Sandbox Documentation

This directory contains documentation for the Cognition Sandbox Backend.

## Files

### [cognition-sandbox-backend.md](./cognition-sandbox-backend.md)

Complete reference guide for the CognitionSandboxBackend, including:
- Architecture overview
- File operations (read, write, edit, ls, glob, grep)
- Command execution
- Local environment setup
- Multi-step task completion examples
- Security considerations
- Troubleshooting guide

## Quick Links

- **Main Documentation**: [cognition-sandbox-backend.md](./cognition-sandbox-backend.md)
- **Source Code**: `server/app/agent/sandbox_backend.py`
- **Agent Factory**: `server/app/agent/cognition_agent.py`

## What is the Sandbox?

The Cognition Sandbox Backend is a local development environment that enables AI agents to:

1. Execute shell commands safely in an isolated workspace
2. Read and write files
3. Search and manipulate code
4. Run tests and other development tools

It's built on top of DeepAgents and implements the `SandboxBackendProtocol`.

## Example Usage

```python
from server.app.agent import create_cognition_agent

# Create an agent with sandbox
agent = create_cognition_agent(
    project_path="/workspace/my-project",
    model=my_llm_model,
)

# The agent can now:
# - Read files: agent uses backend.read("/workspace/file.py")
# - Write files: agent uses backend.write("/workspace/file.py", content)
# - Execute commands: agent uses backend.execute("pytest tests/")
# - Search: agent uses backend.grep_raw("pattern", path="/workspace")
```

## Architecture

```
┌────────────────────────────────────────────────┐
│              Deep Agents Agent                  │
│                                                 │
│   ┌─────────────────────────────────────────┐   │
│   │      CognitionSandboxBackend            │   │
│   │                                         │   │
│   │  ┌─────────────────────────────────┐    │   │
│   │  │      LocalSandbox               │    │   │
│   │  │  - subprocess execution         │    │   │
│   │  │  - workspace isolation          │    │   │
│   │  └─────────────────────────────────┘    │   │
│   └─────────────────────────────────────────┘   │
└────────────────────────────────────────────────┘
```

## Related Documentation

- [Docker Compose Setup](../docker-compose.md) - Running the full stack
- [API Documentation](../api/README.md) - REST API endpoints
- [Agent Architecture](../architecture.md) - System overview

## Contributing

When modifying the sandbox backend:

1. Update this documentation
2. Add tests for new features
3. Verify backwards compatibility
4. Update the troubleshooting section if needed
