# P3 MCP Tool Business Scenarios

End-to-end tests for MCP-backed tool capability.

## Overview

These scenarios test Cognition's supported v0.14 tool path from a business perspective:
- Agent-owned MCP server declarations
- Deployment-controlled MCP transport admission
- Required/optional server discovery behavior
- Canonical MCP tool identity
- Security middleware enforcement around tool calls

## Prerequisites

- Running Cognition server with Agent registry initialized
- MCP outbound transport enabled only for approved origins when MCP scenarios require discovery

## Structure

```
tests/e2e/test_scenarios/p3_tools/
├── __init__.py
└── test_mcp.py                    # MCP tool scenarios
```

## Test Coverage

### MCP discovery and execution
- Agent-owned MCP configs load tools per server
- Optional server failure does not remove healthy server tools
- Required server failure returns a typed, redacted error
- Duplicate canonical tool identities fail discovery
- Disallowed origins fail before discovery

## Running Tests

### Against Docker Compose Environment

The P3-TR tests are designed to run against a live docker-compose environment:

```bash
# Start the docker-compose environment
docker-compose up -d

# Wait for services to be healthy
curl http://localhost:8000/health

# Run all P3 MCP tool scenarios against docker-compose
BASE_URL=http://localhost:8000 pytest tests/e2e/test_scenarios/p3_tools/ -v

# Run specific test file
BASE_URL=http://localhost:8000 pytest tests/e2e/test_scenarios/p3_tools/test_mcp.py -v

# Run CLI tests (requires server running)
pytest tests/e2e/test_scenarios/p3_tools/test_cli_tool_management.py -v

# Run specific test class
BASE_URL=http://localhost:8000 pytest tests/e2e/test_scenarios/p3_tools/test_tool_registry.py::TestToolDiscovery -v
```

### Against Local Test Server (without docker-compose)

```bash
# Run with local test fixtures
pytest tests/e2e/test_scenarios/p3_tools/ -v
```

**Note:** Some tests will skip when run without docker-compose since the AgentRegistry needs to be initialized.

## Business Value Summary

| Scenario | Business Value | P3-TR Item |
|----------|---------------|------------|
| Tool Discovery | Custom tools automatically available | P3-TR-1 |
| Hot Reload | Rapid iteration without restart | P3-TR-2 |
| Tools in Conversations | AI can use specialized tools | P3-TR-3 |
| API Endpoints | Monitoring and debugging visibility | P3-TR-4 |
| Security Middleware | Runtime tool access control | P3-TR-5 |
| Upstream Middleware | Declarative policy enforcement | P3-TR-6 |
| Error Visibility | Developer-friendly error feedback | P3-TR-7 |
| CLI Management | CI/CD integration and automation | P3-TR-8 |
| Directory Auto-Creation | Zero-config setup | P3-TR-9 |

## Related Documentation

- [AGENTS.md](../../../../AGENTS.md) - Agent development guidelines
- [ROADMAP.md](../../../../ROADMAP.md) - P3-TR feature roadmap
