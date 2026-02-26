# P3-ALN Deep Agents Alignment Business Scenarios

End-to-end tests for P3-ALN (Deep Agents Alignment) architecture corrections.

## Overview

These scenarios verify that Cognition correctly uses the upstream deepagents library rather than reimplementing functionality. This ensures we benefit from upstream bug fixes and maintain ecosystem compatibility.

## Structure

```
tests/e2e/test_scenarios/p3_alignment/
├── __init__.py
├── test_sandbox_alignment.py     # P3-ALN-1 & P3-ALN-2: Sandbox backend alignment
└── test_cli_scaffolding.py       # P3-ALN-3: CLI middleware/tool scaffolding
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

## Running Tests

### Against Docker Compose

```bash
# Start the environment
docker-compose up -d

# Run all P3-ALN tests
BASE_URL=http://localhost:8000 pytest tests/e2e/test_scenarios/p3_alignment/test_sandbox_alignment.py -v

# Run CLI scaffolding tests (no server required)
pytest tests/e2e/test_scenarios/p3_alignment/test_cli_scaffolding.py -v

# Run specific test
BASE_URL=http://localhost:8000 pytest tests/e2e/test_scenarios/p3_alignment/test_sandbox_alignment.py::TestSandboxBackendAlignment -v
```

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
