# P3-ALN Deep Agents Alignment Business Scenarios

End-to-end tests for P3-ALN (Deep Agents Alignment) architecture corrections.

## Overview

These scenarios verify that Cognition correctly uses the upstream deepagents library rather than reimplementing functionality. This ensures we benefit from upstream bug fixes and maintain ecosystem compatibility.

## Structure

```
tests/e2e/test_scenarios/p3_alignment/
├── __init__.py
├── test_sandbox_alignment.py     # P3-ALN-1 & P3-ALN-2: Sandbox backend alignment
├── test_cli_scaffolding.py       # P3-ALN-3: CLI middleware/tool scaffolding
├── test_streaming_v2.py          # P3-ALN-4: astream() v2 SSE translation
├── test_agent_definition_wiring.py # P3-ALN-5: AgentDefinition runtime wiring
└── test_deep_agents_capabilities.py # P3-ALN-6: native HITL/planning/response_format exposure
```

## P3-ALN Items Covered

### P3-ALN-1: Sandbox Backend Alignment (`test_sandbox_alignment.py`)
**Business Value:** Uses battle-tested deepagents code instead of custom reimplementation

**Architecture Changes:**
- Subclass `LocalShellBackend` instead of reimplementing
- Use `shlex.split()` + `shell=False` for security
- Remove custom `_resolve_path()` overrides
- Support `virtual_mode` correctly

**Test Scenarios:**
- Sandbox commands execute successfully
- Path resolution uses correct implementation
- Shell injection prevented
- File operations work correctly
- virtual_mode support functional

### P3-ALN-2: Remove ExecutionBackend Protocol (`test_sandbox_alignment.py`)
**Business Value:** Removes ~200 lines of unused adapter indirection

**Architecture Changes:**
- Delete `ExecutionBackend` Protocol
- Delete `ExecutionBackendAdapter`
- Delete `LocalExecutionBackend`
- Keep `DockerExecutionBackend` (Cognition-specific)

**Test Scenarios:**
- Docker backend works without adapter
- No performance degradation
- Live path works correctly

### P3-ALN-3: CLI Middleware Import & Tool Validation (`test_cli_scaffolding.py`)
**Business Value:** Scaffolding commands work without ImportError

**Fixes:**
- Import `AgentMiddleware` from `langchain.agents.middleware.types` (not `deepagents.middleware`)
- Validate tool names are valid Python identifiers
- Fix misleading "Next steps" text

**Test Scenarios:**
- Middleware scaffolding generates valid file
- No ImportError in generated code
- Tool names transformed (hyphens → underscores)
- Invalid identifiers rejected
- Generated code is runnable

### P3-ALN-6: Native Deep Agents Capabilities (`test_deep_agents_capabilities.py`)
**Business Value:** Cognition exposes native Deep Agents planning, structured output, and HITL transport without custom reimplementation.

**Scenarios:**
- Planning events stream from native todo state
- Structured output config is accepted end-to-end for agents and sessions
- Resume endpoint returns the correct API-level contract when no interrupt is active
- Session status accepts `waiting_for_approval`

## Running Tests

### Against Docker Compose

```bash
# Start the environment
docker-compose up -d

# Run all P3-ALN tests
BASE_URL=http://localhost:8000 pytest tests/e2e/test_scenarios/p3_alignment/ -v

# Run CLI scaffolding tests (no server required)
pytest tests/e2e/test_scenarios/p3_alignment/test_cli_scaffolding.py -v

# Run specific test
BASE_URL=http://localhost:8000 pytest tests/e2e/test_scenarios/p3_alignment/test_sandbox_alignment.py::TestSandboxBackendAlignment -v

# Run Deep Agents capability scenarios
BASE_URL=http://localhost:8000 pytest tests/e2e/test_scenarios/p3_alignment/test_deep_agents_capabilities.py -v

# Manual HITL verification against local/docker compose server
uv run python scripts/manual_hitl_check.py --base-url http://localhost:8000
```

## Manual HITL Check

Use `scripts/manual_hitl_check.py` when you want a fast manual proof that HITL works end-to-end against a running server.

What it does:
- creates a session with the chosen agent
- streams a prompt that should trigger a protected tool call
- checks for an `interrupt` SSE event
- verifies the session moves to `waiting_for_approval`
- resumes the run with the requested decision

Example:

```bash
uv run python scripts/manual_hitl_check.py \
  --base-url http://localhost:8000 \
  --agent-name hitl_test \
  --decision approve
```

Note: if the model declines to call the protected tool, the script exits non-zero and prints the streamed events so you can see whether the failure is model behavior or HITL plumbing.

## Business Value Summary

| P3-ALN Item | Architecture Change | Business Impact |
|-------------|---------------------|-----------------|
| P3-ALN-1 | Use LocalShellBackend | Correct path resolution, security fixes |
| P3-ALN-2 | Remove adapter layer | Simpler codebase, no indirection overhead |
| P3-ALN-3 | Fix CLI imports | Working scaffolding commands |

## Related Documentation

- [AGENTS.md](../../../../AGENTS.md) - Architecture guidelines
- [ROADMAP.md](../../../../ROADMAP.md) - P3-ALN roadmap
- [ROADMAP.md#P3-ALN](../../../../ROADMAP.md) - Detailed technical specification
