# Business Scenarios (Python)

Comprehensive end-to-end tests for P2 (Robustness + GUI Extensibility) and P3 (MLflow Integration) features written in Python using pytest.

## Overview

These scenarios test Cognition's P2 features from a **business logic perspective** rather than technical implementation details. Each test validates tangible business value.

## Structure

```
tests/e2e/test_scenarios/
├── conftest.py                 # Shared fixtures and utilities
├── __init__.py
├── p2_cleanup/                 # LangGraph alignment scenarios
│   ├── __init__.py
│   ├── test_conversation_continuity.py
│   ├── test_consistent_data_storage.py
│   ├── test_reliable_streaming.py
│   ├── test_fast_agent_performance.py
│   └── test_project_context_memory.py
├── p2_robustness/              # System resilience scenarios
│   ├── __init__.py
│   ├── test_resilient_streaming.py
│   ├── test_graceful_degradation.py
│   ├── test_quality_monitoring.py
│   ├── test_analytics_tracking.py
│   ├── test_cross_domain_access.py
│   ├── test_isolated_execution.py
│   └── test_project_awareness.py
├── p2_gui/                     # GUI extensibility scenarios
│   ├── __init__.py
│   ├── test_multi_user_management.py
│   ├── test_dynamic_capability.py
│   ├── test_configuration_updates.py
│   └── test_tool_scaffolding.py
└── p3_mlflow/                  # MLflow integration scenarios
    ├── __init__.py
    ├── test_tracing_integration.py
    ├── test_evaluation.py
    └── test_feedback_loop.py
```

## Quick Start

### Prerequisites

- Running Cognition server (default: http://localhost:8000)
- pytest and pytest-asyncio installed
- httpx for HTTP client

### Installation

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Or using uv
uv add --dev pytest pytest-asyncio httpx
```

### Running Tests

```bash
# Run all P2 scenarios
pytest tests/e2e/test_scenarios/ -v

# Run specific category
pytest tests/e2e/test_scenarios/p2_cleanup/ -v
pytest tests/e2e/test_scenarios/p2_robustness/ -v
pytest tests/e2e/test_scenarios/p2_gui/ -v

# Run specific scenario
pytest tests/e2e/test_scenarios/p2_cleanup/test_conversation_continuity.py -v

# Run with custom server URL
BASE_URL=http://my-server:8000 pytest tests/e2e/test_scenarios/ -v

# Run with detailed output
pytest tests/e2e/test_scenarios/ -v -s
```

## Scenario Categories

### P2 Cleanup (5 scenarios)

Tests LangGraph alignment and cleanup items:

1. **Conversation Continuity** - Ensures conversations persist across server restarts
2. **Consistent Data Storage** - Validates reliable data operations
3. **Reliable Streaming** - Tests SSE resilience during network issues
4. **Fast Agent Performance** - Verifies agent caching improves response times
5. **Project Context Memory** - Confirms AI remembers project context

### P2 Robustness (7 scenarios)

Tests system resilience features:

1. **Resilient Streaming** - Conversations survive disconnections
2. **Graceful Degradation** - AI provider failures don't interrupt work
3. **Quality Monitoring** - Quality tracking and evaluation
4. **Analytics Tracking** - Cost optimization and usage analytics
5. **Cross-Domain Access** - CORS for custom frontend integrations
6. **Isolated Execution** - Safe execution of AI-generated code
7. **Project Awareness** - Contextually relevant AI suggestions

### P2 GUI (4 scenarios)

Tests GUI extensibility features:

1. **Multi-User Management** - Team leaders oversee conversations
2. **Dynamic Capability Extension** - Runtime tool registration
3. **Configuration Updates** - Zero-downtime config changes
4. **Tool Scaffolding** - Rapid custom tool development

### P3 MLflow (3 scenarios, 20 tests)

Tests MLflow integration for observability and evaluation:

1. **Tracing Integration** (8 tests) - Automatic trace capture in MLflow
2. **Evaluation & Scoring** (7 tests) - Quality assessment with MLflow scorers
3. **Human Feedback Loop** (7 tests) - Feedback collection and datasets

**Note:** P3 scenarios require MLflow to be enabled and running. Tests skip gracefully when MLflow is not available.

## Running P3 MLflow Scenarios

```bash
# Run only MLflow scenarios (requires MLflow server)
pytest tests/e2e/test_scenarios/p3_mlflow/ -v

# Skip MLflow scenarios (run without MLflow)
pytest tests/e2e/test_scenarios/ -v -m "not mlflow"

# Run all scenarios including MLflow
pytest tests/e2e/test_scenarios/ -v
```

## Business Value Summary

| Scenario | Business Value | P2 Item |
|----------|---------------|---------|
| Conversation Continuity | Users don't lose work when server restarts | P2-CLEANUP-1 |
| Consistent Data Storage | Reliable, trustworthy data operations | P2-CLEANUP-2 |
| Reliable Streaming | Uninterrupted streaming during network issues | P2-CLEANUP-3 |
| Fast Agent Performance | Quick AI responses through caching | P2-CLEANUP-5 |
| Project Context Memory | AI remembers project context | P2-CLEANUP-4 |
| Resilient Streaming | Conversations survive disconnections | P2-1: SSE Reconnection |
| Graceful Degradation | AI provider failures don't interrupt | P2-2: Circuit Breaker |
| Quality Monitoring | Data-driven quality assurance | P2-3: Evaluation Pipeline |
| Analytics Tracking | Cost optimization and analytics | P2-5: Enrich Message Model |
| Cross-Domain Access | Custom frontends securely | P2-4: CORS Middleware |
| Isolated Execution | Safe AI-generated code | P2-6: ExecutionBackend |
| Project Awareness | Contextually relevant suggestions | P2-7: ContextManager |
| Multi-User Management | Team leaders oversee conversations | P2-8: SessionManager |
| Dynamic Capability Extension | Runtime tool registration | P2-9: AgentRegistry |
| Configuration Updates | Zero-downtime config changes | P2-10: File Watcher |
| Tool Scaffolding | Rapid custom tool development | P2-11: CLI Scaffolding |
| Tracing Integration | Complete agent observability | P3-1: MLflow Tracing |
| Evaluation & Scoring | Systematic quality assessment | P3-1: MLflow Evaluation |
| Human Feedback Loop | Human-in-the-loop improvement | P3-7: MLflow Feedback |

## Fixtures

### api_client

Provides a configured `ScenarioTestClient` for making API requests:

```python
async def test_example(api_client):
    response = await api_client.get("/health")
    assert response.status_code == 200
```

### session

Provides a pre-created session ID:

```python
async def test_example(session):
    # Use session directly
    print(f"Testing with session: {session}")
```

### timer

Provides a performance timer:

```python
async def test_performance(api_client, session, timer):
    timer.start()
    await api_client.send_message(session, "Test")
    duration = timer.stop()
    print(f"Took {duration:.0f}ms")
```

## Writing New Scenarios

1. Create a new file in the appropriate category directory
2. Name it `test_<business_scenario>.py`
3. Use the `@pytest.mark.asyncio` decorator
4. Use the fixtures from conftest.py
5. Follow the existing pattern of business-focused test methods

Example:

```python
"""Business Scenario: <Name>

<Business description and value>
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class Test<ScenarioName>:
    """Test <business goal>."""
    
    async def test_<phase>(
        self,
        api_client,
        session
    ) -> None:
        """<Phase description>."""
        # Test implementation
        response = await api_client.send_message(session, "Test")
        assert response.status_code == 200
```

## CI/CD Integration

```yaml
# Example GitHub Actions
- name: Run P2 Scenarios
  run: |
    docker-compose up -d
    sleep 30  # Wait for services
    pytest tests/e2e/test_scenarios/ -v
```

## Troubleshooting

### Connection Errors

Ensure the Cognition server is running:

```bash
curl http://localhost:8000/ready
```

### Timeout Errors

Some scenarios may take longer. Increase timeout:

```bash
pytest tests/e2e/test_scenarios/ -v --timeout=120
```

### Session Scoping Issues

If tests fail with 403 errors, scoping may be enabled. The test client automatically detects and uses scoping when enabled.

## Success Criteria

All scenarios should pass against a properly configured Cognition server:

### P2 Scenarios (Core System)
- **Cleanup Scenarios:** 5/5 passing
- **Robustness Scenarios:** 7/7 passing
- **GUI Scenarios:** 4/4 passing
- **P2 Total:** 16/16 scenarios passing

### P3 Scenarios (MLflow Integration - Optional)
- **Tracing Integration:** 8/8 tests passing (requires MLflow)
- **Evaluation & Scoring:** 7/7 tests passing (requires MLflow)
- **Human Feedback Loop:** 7/7 tests passing (requires MLflow)
- **P3 Total:** 22/22 tests passing (when MLflow enabled)

**Note:** P3 scenarios skip gracefully when MLflow is not available. Core functionality works without MLflow.

## Related Documentation

- [AGENTS.md](../../../../AGENTS.md) - Agent development guidelines
- [ROADMAP.md](../../../../ROADMAP.md) - P2 feature roadmap
- [scenarios/README.md](../../../../scenarios/README.md) - Bash scenario documentation
