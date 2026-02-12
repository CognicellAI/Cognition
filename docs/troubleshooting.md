# Troubleshooting Guide

Common issues and solutions for Cognition.

## Installation Issues

### Error: `ModuleNotFoundError: No module named 'cognition'`

**Cause**: Package not installed or installed in wrong environment.

**Solution**:
```bash
# Install in editable mode
pip install -e .

# Or with uv
uv pip install -e .
```

### Error: `uv: command not found`

**Cause**: `uv` is not installed.

**Solution**:
```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or with pip
pip install uv
```

### Error: `ImportError: No module named 'prometheus_client'`

**Cause**: Missing optional dependency.

**Solution**:
```bash
pip install prometheus-client
```

## Server Issues

### Server fails to start

**Symptoms**: `cognition serve` crashes immediately.

**Checklist**:
1. Check port is not in use:
   ```bash
   lsof -i :8000  # macOS/Linux
   netstat -ano | findstr :8000  # Windows
   ```

2. Check configuration is valid:
   ```bash
   cognition config
   ```

3. Check logs for errors:
   ```bash
   cognition serve --log-level debug
   ```

**Common Causes**:

- **Port already in use**: Change port with `cognition serve --port 8080`
- **Invalid configuration**: Check `~/.cognition/config.yaml` syntax
- **Missing dependencies**: Run `pip install -e ".[all]"`

### Error: `Failed to create project directory`

**Cause**: Insufficient permissions or invalid path.

**Solution**:
```bash
# Check workspace directory exists and is writable
ls -la ./workspaces  # or your configured workspace root

# Create if needed
mkdir -p ./workspaces

# Or change workspace location
export COGNITION_WORKSPACE_ROOT=/path/to/writable/dir
```

### Server starts but client can't connect

**Symptoms**: Server running but TUI shows "Disconnected".

**Checklist**:
1. Check server is actually running:
   ```bash
   curl http://127.0.0.1:8000/health
   ```

2. Check client is using correct URL:
   ```bash
   echo $COGNITION_SERVER_URL  # Should be http://127.0.0.1:8000
   ```

3. Check firewall/network settings

**Solution**:
```bash
# Set correct server URL
export COGNITION_SERVER_URL=http://127.0.0.1:8000

# Or specify when running client
COGNITION_SERVER_URL=http://127.0.0.1:8000 cognition-client
```

## LLM Provider Issues

### Error: `No API key provided`

**Cause**: API key not set for the configured provider.

**Solution**:

For OpenAI:
```bash
export OPENAI_API_KEY="sk-..."
```

For AWS Bedrock:
```bash
export AWS_ACCESS_KEY_ID="AKIA..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-east-1"
```

For OpenAI-compatible (OpenRouter, etc.):
```bash
export COGNITION_OPENAI_COMPATIBLE_API_KEY="sk-or-..."
```

### Error: `Invalid API key`

**Cause**: API key is incorrect or expired.

**Solution**:
1. Verify key is correct
2. Check key has not expired
3. For OpenAI: Check at https://platform.openai.com/api-keys
4. For AWS: Verify IAM permissions

### Error: `Rate limit exceeded`

**Cause**: Too many requests to LLM provider.

**Solutions**:
1. Wait and retry
2. Reduce `rate_limit.per_minute` in config
3. Upgrade your LLM provider plan
4. Use mock provider for testing:
   ```yaml
   llm:
     provider: mock
   ```

### Error: `Model not found`

**Cause**: Invalid model name for provider.

**Solution**:
Check available models for your provider:

**OpenAI**: gpt-4o, gpt-4o-mini, gpt-4-turbo, etc.
**Bedrock**: anthropic.claude-3-sonnet-20240229-v1:0, etc.
**OpenRouter**: Check https://openrouter.ai/models

Update config:
```yaml
llm:
  provider: openai
  model: gpt-4o  # Use valid model name
```

## Client Issues

### TUI shows "Disconnected"

**Checklist**:
1. Server is running: `curl http://127.0.0.1:8000/health`
2. Correct URL: `echo $COGNITION_SERVER_URL`
3. No firewall blocking

**Solution**:
```bash
# Restart both
# Terminal 1
cognition serve

# Terminal 2
cognition-client
```

### TUI freezes or becomes unresponsive

**Cause**: SSE stream blocking or large output.

**Solution**:
1. Press `Ctrl+C` to interrupt
2. Restart client
3. Try with shorter prompts

### Error: `Failed to send message`

**Checklist**:
1. Session is active: Check session ID in TUI header
2. Server is responsive: Test with `cognition health`
3. Check server logs for errors

## Configuration Issues

### Error: `Invalid configuration`

**Cause**: YAML syntax error or invalid values.

**Solution**:
1. Validate YAML syntax:
   ```bash
   python3 -c "import yaml; yaml.safe_load(open('~/.cognition/config.yaml'))"
   ```

2. Check for common errors:
   - Tabs instead of spaces (use spaces)
   - Missing quotes around special characters
   - Invalid values (e.g., temperature > 2.0)

### Changes to config not taking effect

**Cause**: Server needs restart or wrong config file.

**Solution**:
```bash
# Check which config files are loaded
cognition config

# Restart server after config changes
cognition serve

# For project config, ensure you're in project directory
pwd
cat .cognition/config.yaml
```

## API Issues

### Error: `404 Not Found`

**Cause**: Endpoint doesn't exist or wrong URL.

**Solution**:
1. Check OpenAPI docs at `http://localhost:8000/docs`
2. Verify URL path is correct
3. Check server is running correct version

### Error: `422 Validation Error`

**Cause**: Request body doesn't match schema.

**Solution**:
1. Check request format in OpenAPI docs
2. Ensure required fields are present
3. Validate data types (e.g., numbers not strings)

Example fix:
```bash
# Wrong - missing required field
curl -X POST http://localhost:8000/sessions \
  -d '{"title": "Test"}'

# Correct - includes project_id
curl -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"project_id": "abc", "title": "Test"}'
```

### SSE stream not working

**Cause**: Client not accepting SSE format.

**Solution**:
Ensure `Accept: text/event-stream` header:

```bash
curl -X POST http://localhost:8000/sessions/123/messages \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"content": "Hello"}'
```

## Performance Issues

### Slow response times

**Possible Causes**:
1. LLM provider latency
2. Large file operations
3. Complex tool executions

**Solutions**:
1. Check LLM provider status
2. Use faster model (e.g., gpt-4o-mini)
3. Reduce `max_iterations` in config
4. Clear old sessions:
   ```bash
   # Delete old sessions via API
   curl -X DELETE http://localhost:8000/sessions/<id>
   ```

### High memory usage

**Cause**: Too many concurrent sessions or large workspaces.

**Solutions**:
1. Reduce `max_sessions` in config
2. Delete old projects:
   ```bash
   rm -rf ./workspaces/old-project-*
   ```
3. Restart server periodically

## Testing Issues

### E2E tests fail

**Checklist**:
1. Server not running: Tests start their own server
2. Port conflicts: Tests use random ports
3. Dependencies: `pip install -e ".[test]"`

**Run tests with debugging**:
```bash
pytest tests/e2e/ -v --tb=short -x
```

### Unit tests fail

**Checklist**:
1. Missing dependencies: `pip install -e ".[test]"`
2. Wrong Python version: Need Python 3.11+

**Run specific test**:
```bash
pytest tests/unit/test_rest_api.py::TestProjectEndpoints -v
```

## Development Issues

### Type checking errors

Run mypy:
```bash
mypy server/ client/
```

### Linting errors

Run ruff:
```bash
ruff check server/ client/
ruff format server/ client/
```

## Getting Help

If you're still stuck:

1. **Check logs**: Run with `--log-level debug`
2. **Check documentation**: See `http://localhost:8000/docs`
3. **Run health check**: `cognition health`
4. **Test with curl**: Verify API directly
5. **Check GitHub Issues**: Search for similar problems

## Debug Mode

Enable debug logging:

```bash
# Server
cognition serve --log-level debug

# Or via config
export COGNITION_LOG_LEVEL=debug
```

This will show:
- All HTTP requests
- LLM API calls
- Tool executions
- Session state changes

## Common Error Messages

| Error | Cause | Solution |
|-------|-------|----------|
| `Connection refused` | Server not running | Start server with `cognition serve` |
| `ModuleNotFoundError` | Missing dependency | `pip install -e .` |
| `No API key` | Missing API key | Set appropriate API key env var |
| `Rate limited` | Too many requests | Wait or reduce rate limit |
| `Invalid config` | YAML error | Validate YAML syntax |
| `Port in use` | Port occupied | Change port or kill process |
| `Permission denied` | File permissions | Check workspace directory permissions |

## Still Having Issues?

1. Search existing GitHub issues
2. Create a new issue with:
   - Error message
   - Steps to reproduce
   - Configuration (sanitized)
   - Server logs
   - Client version (`cognition --version`)
