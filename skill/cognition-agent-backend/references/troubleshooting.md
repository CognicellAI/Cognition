# Troubleshooting

Common issues and how to resolve them.

## Server Startup

### "Port already in use"
**Symptoms:** `Address already in use` error.
**Solution:**
1.  Check port 8000: `lsof -i :8000`
2.  Kill process: `kill -9 <PID>`

### "Unknown LLM provider"
**Symptoms:** `ProviderNotFound: 'foo'`
**Solution:**
Ensure `COGNITION_LLM_PROVIDER` is set to a registered provider:
- `mock`
- `openai`
- `bedrock`
- `ollama`
- `openai_compatible`

Register custom providers *before* `create_cognition_agent` is called.

## Runtime Issues

### Rate Limit Exceeded
**Symptoms:** `429 Too Many Requests`
**Solution:**
Increase `COGNITION_RATE_LIMIT_PER_MINUTE` (default: 60) in `.env`.

### Docker Sandbox Failure
**Symptoms:** `DockerException: Error starting container`
**Solution:**
1.  Verify Docker is running: `docker info`
2.  Build the sandbox image: `docker build -f Dockerfile.sandbox -t cognition-sandbox:latest .`
3.  Check memory limits (`COGNITION_DOCKER_MEMORY_LIMIT`).

### SSE Disconnects
**Symptoms:** Stream stops abruptly.
**Solution:**
-   Ensure client handles `Last-Event-ID` for reconnection.
-   Check proxy timeouts (Nginx/Cloudflare) - SSE requires long-lived connections.

### Database Connection Failed
**Symptoms:** `OperationalError` or connection timeout.
**Solution:**
-   Verify `COGNITION_PERSISTENCE_URI`.
-   Ensure PostgreSQL container is healthy.
-   Run `cognition db upgrade` to apply schema changes.

## Debugging

Enable verbose logging:

```bash
COGNITION_LOG_LEVEL=debug cognition serve
```

Inspect internal state:

```bash
# Dump session state to JSON
curl http://localhost:8000/sessions/{id}
```
