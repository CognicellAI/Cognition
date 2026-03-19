# Provider Fallback E2E Tests

Comprehensive end-to-end tests for the Cognition provider fallback system, testing real Agent interactions with the live docker-compose environment.

## Prerequisites

### 1. Docker Environment

Ensure Docker and docker-compose are installed and running:

```bash
docker --version
docker-compose --version
```

### 2. Start the Environment

Start the full Cognition stack:

```bash
docker-compose up -d
```

This starts:
- Cognition server (port 8000)
- PostgreSQL database (port 5432)
- MLflow tracking (port 5050)
- Prometheus metrics (port 9091)
- Grafana dashboards (port 3000)
- OpenTelemetry collector (port 4317)
- Loki log aggregation (port 3100)

### 3. API Credentials (Optional but Recommended)

For full provider fallback tests, you need an OpenRouter API key:

1. Sign up at [OpenRouter](https://openrouter.ai/)
2. Get your API key from the dashboard
3. Add to your `.env` file:

```bash
COGNITION_OPENAI_COMPATIBLE_API_KEY=sk-or-v1-...
```

The docker-compose.yml is already configured to use OpenRouter as the default provider.

## Test Coverage

### Core Provider Fallback Tests (`test_provider_fallback_e2e.py`)

#### Provider Management
- ✅ `test_create_single_provider_and_chat` - Create provider and use for Agent chat
- ✅ `test_provider_priority_ordering` - Multiple providers sorted by priority
- ✅ `test_disabled_provider_not_used` - Disabled providers skipped in fallback
- ✅ `test_patch_provider_updates_priority` - Dynamic priority updates

#### Agent Interactions
- ✅ `test_agent_with_custom_provider_chain` - Custom agent with provider fallback
- ✅ `test_streaming_with_provider_fallback` - Streaming responses with fallback
- ✅ `test_conversation_continuity_across_providers` - History persists across switches

#### Hot Reloading
- ✅ `test_provider_changes_immediate_effect` - Changes without restart
- ✅ `test_delete_provider_runtime_effect` - Deletion takes effect immediately

#### Credentials & Security
- ✅ `test_api_key_env_resolution` - Environment variable API key resolution
- ✅ `test_invalid_api_key_fails_gracefully` - Graceful auth failures

#### Edge Cases
- ✅ `test_no_providers_configured` - Global defaults fallback
- ✅ `test_all_providers_disabled` - Error handling when all disabled
- ✅ `test_provider_with_invalid_base_url` - Fallback to valid providers

### Advanced Scenarios (`test_advanced_scenarios.py`)

#### Performance & Concurrency
- ✅ `test_large_provider_chain_performance` - 10+ provider chains
- ✅ `test_concurrent_provider_operations` - Concurrent CRUD operations
- ✅ `test_rapid_provider_updates` - Rapid sequential updates
- ✅ `test_provider_id_special_characters` - Various ID patterns

#### Session Overrides
- ✅ `test_session_provider_override` - Session-level provider selection

## Running the Tests

### Run All E2E Tests

```bash
# Start the environment first
docker-compose up -d

# Run all e2e tests
uv run pytest tests/e2e/test_scenarios/p2_provider_fallback/ -v
```

### Run Specific Test File

```bash
# Main fallback tests
uv run pytest tests/e2e/test_scenarios/p2_provider_fallback/test_provider_fallback_e2e.py -v

# Advanced scenarios
uv run pytest tests/e2e/test_scenarios/p2_provider_fallback/test_advanced_scenarios.py -v
```

### Run with Specific Markers

```bash
# Only tests that don't require credentials
uv run pytest tests/e2e/test_scenarios/p2_provider_fallback/ -v -m "not credentials"

# Only provider fallback tests
uv run pytest tests/e2e/test_scenarios/p2_provider_fallback/ -v -m "provider_fallback"
```

### Run with Live Credentials

```bash
# Export your API key
export COGNITION_OPENAI_COMPATIBLE_API_KEY=sk-or-v1-...

# Run tests that require credentials
uv run pytest tests/e2e/test_scenarios/p2_provider_fallback/ -v -m "credentials"
```

## Test Data

Tests use OpenRouter's free tier models:
- `google/gemini-2.0-flash-exp:free` - Primary test model
- `meta-llama/llama-3.2-3b-instruct:free` - Fallback model
- `mistralai/mistral-7b-instruct:free` - Additional fallback

These models are rate-limited but free to use. Tests include timeouts and retries where appropriate.

## Troubleshooting

### Tests Failing with 401/403

**Cause:** Missing or invalid OpenRouter API key
**Solution:** 
1. Check your `.env` file has `COGNITION_OPENAI_COMPATIBLE_API_KEY`
2. Verify the key is valid at OpenRouter dashboard
3. Restart docker-compose after updating `.env`:
   ```bash
   docker-compose down
   docker-compose up -d
   ```

### Tests Failing with 502/503

**Cause:** Server not ready or provider unavailable
**Solution:**
1. Check server health:
   ```bash
   curl http://localhost:8000/health
   ```
2. Wait for services to be ready:
   ```bash
   docker-compose ps
   ```
3. Check logs:
   ```bash
   docker-compose logs -f cognition
   ```

### Rate Limiting

OpenRouter free tier has rate limits. If you see rate limit errors:
1. Wait a few minutes between test runs
2. Consider upgrading to a paid tier for CI/CD
3. Tests are designed to be idempotent - safe to retry

### Database Connection Issues

**Cause:** PostgreSQL not ready
**Solution:**
```bash
# Check PostgreSQL health
docker-compose ps postgres

# Restart if needed
docker-compose restart postgres
sleep 10  # Wait for init
docker-compose restart cognition
```

## Architecture

```
┌─────────────────────────────────────────────┐
│         Cognition E2E Test Suite            │
├─────────────────────────────────────────────┤
│                                             │
│  Test Classes:                              │
│  ├─ TestProviderFallbackChain              │
│  │   ├─ Provider CRUD operations           │
│  │   └─ Priority ordering                  │
│  ├─ TestScopedProviderFallback             │
│  │   └─ Multi-tenant scoping               │
│  ├─ TestProviderFallbackAgentInteraction   │
│  │   ├─ Custom agents with fallback        │
│  │   ├─ Streaming responses                │
│  │   └─ Conversation continuity            │
│  ├─ TestProviderFallbackHotReload          │
│  │   └─ Runtime configuration changes      │
│  ├─ TestProviderFallbackCredentials        │
│  │   └─ API key resolution                 │
│  └─ TestProviderFallbackEdgeCases          │
│      └─ Error handling                     │
│                                             │
│  Advanced:                                  │
│  ├─ TestAdvancedProviderScenarios          │
│  └─ TestProviderFallbackSessionOverrides   │
│                                             │
└─────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────┐
│         Docker Compose Environment          │
├─────────────────────────────────────────────┤
│                                             │
│  Services:                                  │
│  ├─ Cognition Server (FastAPI)     :8000   │
│  ├─ PostgreSQL                     :5432   │
│  ├─ MLflow Tracking                :5050   │
│  ├─ Prometheus                     :9091   │
│  ├─ Grafana                        :3000   │
│  ├─ OpenTelemetry Collector        :4317   │
│  └─ Loki                           :3100   │
│                                             │
│  Provider:                                  │
│  └─ OpenRouter (openai_compatible)         │
│      ├─ Base URL: openrouter.ai/api/v1    │
│      └─ Models: Gemini, Llama, Mistral    │
│                                             │
└─────────────────────────────────────────────┘
```

## Contributing

When adding new e2e tests:

1. **Use unique IDs**: Always use `_unique()` helper to avoid test contamination
2. **Clean up**: Always delete resources in `finally` blocks
3. **Mark appropriately**: Use `@pytest.mark.e2e` and `@pytest.mark.provider_fallback`
4. **Handle credentials**: Use `openrouter_required` marker for tests needing API keys
5. **Document**: Add docstrings explaining business value

Example:

```python
@pytest.mark.asyncio
@pytest.mark.e2e
@pytest.mark.provider_fallback
class TestMyNewFeature:
    """Brief description of business value."""
    
    async def test_something(self, api_client) -> None:
        """Test description."""
        resource_id = _unique("myfeature")
        
        try:
            # Test code
            pass
        finally:
            # Cleanup
            await api_client.delete(f"/resource/{resource_id}")
```

## Related Documentation

- [Main E2E Tests](../conftest.py) - Shared fixtures and configuration
- [Provider Lifecycle](../p2_config_registry/test_provider_lifecycle.py) - Provider CRUD tests
- [Agent Lifecycle](../p2_config_registry/test_agent_lifecycle.py) - Agent CRUD tests
- [API Documentation](../../../../server/app/api/README.md) - API endpoints
