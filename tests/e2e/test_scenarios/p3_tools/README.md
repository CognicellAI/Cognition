# P3 Tools Business Scenarios

End-to-end tests for P3-TR (Tool Registry End-to-End) features.

## Overview

These scenarios test Cognition's tool registry functionality from a business perspective:
- Tool discovery from `.cognition/tools/`
- Hot-reload via file watcher
- API endpoints for tool inspection
- CLI tool management commands
- Security middleware enforcement

## Prerequisites

- Running Cognition server with AgentRegistry initialized
- `.cognition/tools/` directory configured (auto-created if missing)

## Structure

```
tests/e2e/test_scenarios/p3_tools/
├── __init__.py
├── test_tool_registry.py          # Core tool registry scenarios
└── test_cli_tool_management.py    # CLI command scenarios
```

## Test Coverage

### P3-TR-1: Tool Discovery Logic
- `test_tools_endpoint_returns_registered_tools` - GET /tools returns tool list
- `test_tool_has_required_fields` - Tools have name, source, module fields
- `test_get_specific_tool_detail` - GET /tools/{name} returns specific tool
- `test_get_nonexistent_tool_returns_404` - 404 for unknown tools

### P3-TR-2: File Watcher + Hot Reload
- `test_file_watcher_triggers_reload` - File changes trigger reload
- `test_reload_returns_error_count` - Reload endpoint returns errors

### P3-TR-3: Tools in Conversations
- `test_session_with_tools_can_execute` - Sessions can use registered tools
- `test_tools_listed_in_session_context` - Tools available to sessions

### P3-TR-4: Tool API Endpoints
- Covered in discovery tests above

### P3-TR-5: Tool Security Middleware
- `test_blocked_tool_not_available` - Blocked tools not in registry
- `test_tool_audit_logging` - Tool calls are logged

### P3-TR-6: Upstream Middleware Configuration
- `test_agent_with_middleware_loads` - Agents with middleware load successfully
- `test_tool_retry_middleware_available` - Retry middleware can be configured
- `test_pii_middleware_available` - PII redaction available

### P3-TR-7: Tool Load Error Visibility
- `test_tool_errors_endpoint_exists` - GET /tools/errors available
- `test_error_format_has_required_fields` - Errors have file, type, message, timestamp
- `test_reload_clears_previous_errors` - Reload clears old errors

### P3-TR-8: CLI Tool Management
- `test_tools_list_command_exists` - cognition tools list available
- `test_tools_reload_command_exists` - cognition tools reload available
- `test_tools_list_connects_to_server` - CLI connects to API
- `test_tools_list_exits_with_error_on_load_errors` - Exit code 1 on errors
- `test_tools_reload_triggers_server_reload` - Reload triggers server reload
- `test_full_tool_management_workflow` - Complete list → reload → list workflow

### P3-TR-9: Directory Auto-Creation
- `test_config_endpoint_creates_cognition_dir` - .cognition/ created automatically
- `test_tools_endpoint_works_without_manual_setup` - Works without manual directory creation

## Running Tests

### Against Docker Compose Environment

The P3-TR tests are designed to run against a live docker-compose environment:

```bash
# Start the docker-compose environment
docker-compose up -d

# Wait for services to be healthy
curl http://localhost:8000/health

# Run all P3 tool scenarios against docker-compose
BASE_URL=http://localhost:8000 pytest tests/e2e/test_scenarios/p3_tools/ -v

# Run specific test file
BASE_URL=http://localhost:8000 pytest tests/e2e/test_scenarios/p3_tools/test_tool_registry.py -v

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
