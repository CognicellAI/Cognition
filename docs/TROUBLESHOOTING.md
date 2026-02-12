# Troubleshooting Guide

Common issues and solutions for Cognition.

## Table of Contents

1. [Installation Issues](#installation-issues)
2. [Connection Issues](#connection-issues)
3. [Session Issues](#session-issues)
4. [LLM Issues](#llm-issues)
5. [Container Issues](#container-issues)
6. [Performance Issues](#performance-issues)
7. [Project Issues](#project-issues)
8. [Getting Help](#getting-help)

---

## Installation Issues

### Issue: "uv: command not found"

**Symptom:**
```bash
$ uv pip install -e ".[all]"
bash: uv: command not found
```

**Solution:**
```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Add to PATH (follow the output instructions)
# Usually:
export PATH="$HOME/.cargo/bin:$PATH"

# Verify
uv --version
```

### Issue: "Docker daemon is not running"

**Symptom:**
```bash
$ docker ps
Cannot connect to the Docker daemon
```

**Solution:**

**macOS:**
```bash
# Start Docker Desktop
open -a Docker

# Wait for it to start (check menu bar icon)
```

**Linux:**
```bash
# Start Docker service
sudo systemctl start docker

# Or if using Docker Desktop
systemctl --user start docker-desktop

# Add user to docker group (to avoid sudo)
sudo usermod -aG docker $USER
# Log out and back in
```

**Windows (WSL2):**
```bash
# In WSL2, Docker Desktop should be running on Windows side
# Make sure Docker Desktop integration is enabled for WSL2
```

### Issue: "Failed to build Docker image"

**Symptom:**
```bash
$ make build-agent-image
Error: failed to solve: rpc error: code = Unknown desc = executor failed running...
```

**Solution:**
```bash
# Clean up Docker and retry
docker system prune -f
docker builder prune -f

# Build with no cache
docker build --no-cache -t opencode-agent:py -f docker/Dockerfile.agent .

# Check Docker daemon has enough disk space
docker system df
```

---

## Connection Issues

### Issue: "Connection refused" when starting TUI

**Symptom:**
```
Connection error: [Errno 61] Connection refused
```

**Solution:**
```bash
# 1. Check if server is running
curl http://localhost:8000/health

# 2. If not running, start it
cd server && uv run uvicorn app.main:app --reload --port 8000

# 3. Check server logs for errors
cd server && uv run uvicorn app.main:app --port 8000 2>&1 | head -50

# 4. Verify port is not in use
lsof -i :8000
# If in use, kill the process or use different port
```

### Issue: "WebSocket connection closed unexpectedly"

**Symptom:**
TUI shows "Connection closed" or disconnects frequently.

**Solution:**
```bash
# Check server health
curl http://localhost:8000/health

# Check server logs for errors
tail -f server.log

# Common causes:
# 1. Server crashed - restart it
# 2. Timeout - increase timeout in .env
CONTAINER_TIMEOUT=600  # 10 minutes

# 3. Memory issue - check memory usage
docker stats --no-stream

# 4. Network issue - test connectivity
ping localhost
```

---

## Session Issues

### Issue: "No active session. Use /create first"

**Symptom:**
Typing a message shows error about no active session.

**Solution:**
```
# Create a session first
> /create my-project

# Or for ephemeral session
> /create

# Then you can send messages
> Show me the Python files
```

### Issue: Session creation is slow

**Symptom:**
`/create` takes more than 10 seconds.

**Solution:**
```bash
# Check Docker is responsive
docker ps

# Check if agent image exists
docker images | grep opencode-agent

# If not, build it
make build-agent-image

# Check disk I/O (slow disk can cause delays)
iostat -x 1 5

# Check if workspace directory is on slow storage
# Move to SSD if possible
```

### Issue: "Session ID mismatch"

**Symptom:**
Error about session ID not matching.

**Solution:**
```bash
# This usually happens if:
# 1. Server was restarted
# 2. Session expired
# 3. Multiple TUI instances

# Solution: Create a new session
> /create my-project
```

---

## LLM Issues

### Issue: "No LLM configured"

**Symptom:**
Server logs show: "No LLM configured"

**Solution:**
```bash
# Check .env file exists
cat .env

# Add your API key
echo "OPENAI_API_KEY=sk-your-key-here" >> .env

# Verify settings
uv run python -c "from server.app.settings import get_settings; s = get_settings(); print(f'Provider: {s.llm_provider}')"

# Restart server after editing .env
```

### Issue: "API key invalid"

**Symptom:**
```
Error: 401 Unauthorized - Invalid API key
```

**Solution:**
```bash
# Test API key directly

# OpenAI
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"

# Anthropic
curl https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01"

# If test fails, get new key from provider dashboard
```

### Issue: "Rate limit exceeded"

**Symptom:**
```
Error: 429 Too Many Requests
```

**Solution:**
```bash
# Wait a bit and retry

# Or switch to different model (faster/cheaper)
# Edit .env:
DEFAULT_MODEL=gpt-3.5-turbo  # Instead of gpt-4

# Or use local model (no rate limits)
LLM_PROVIDER=openai_compatible
OPENAI_API_BASE=http://localhost:11434/v1
DEFAULT_MODEL=llama2
```

### Issue: LLM is very slow

**Symptom:**
Agent takes 30+ seconds to respond.

**Solution:**
```bash
# Check which model you're using
grep DEFAULT_MODEL .env

# Use faster model
DEFAULT_MODEL=gpt-3.5-turbo  # Fast
# vs
DEFAULT_MODEL=gpt-4  # Slow but better quality

# Or use local model (depends on your hardware)
# See USER_GUIDE.md for local model setup

# Check internet connection
ping api.openai.com
```

---

## Container Issues

### Issue: "Container exited with code 1"

**Symptom:**
Agent can't run commands in container.

**Solution:**
```bash
# Check container logs
docker logs $(docker ps -lq)

# Check if agent image is correct
docker run --rm opencode-agent:py python --version

# Rebuild if needed
make build-agent-image

# Check workspace permissions
ls -la workspaces/

# Verify workspace is mounted correctly
docker inspect $(docker ps -lq) | grep -A5 Mounts
```

### Issue: "Permission denied" when accessing files

**Symptom:**
Agent can't read/write files in workspace.

**Solution:**
```bash
# Fix workspace permissions
sudo chown -R $USER:$USER workspaces/
chmod -R 755 workspaces/

# Check Docker user mapping
# Container runs as UID 1000, ensure workspace is accessible
docker run --rm -v $(pwd)/workspaces:/workspace opencode-agent:py ls -la /workspace

# If permission issues persist, run container as root (development only)
# Edit container creation in server/app/executor/container.py
```

### Issue: Container uses too much memory

**Symptom:**
System slows down, OOM errors.

**Solution:**
```bash
# Check container memory usage
docker stats --no-stream

# Limit container memory in .env
CONTAINER_MEMORY_LIMIT=1g  # Reduce from 2g

# Stop unused containers
docker container prune -f

# Limit max concurrent sessions
MAX_SESSIONS=10  # Instead of 100
```

---

## Performance Issues

### Issue: Everything is slow

**Symptom:**
Session creation, file operations, agent responses are all slow.

**Solution:**
```bash
# 1. Check system resources
htop
# or
top

# 2. Check disk usage (should be < 80%)
df -h

# 3. Check disk I/O (shouldn't be constantly high)
iostat -x 1

# 4. Check network latency
ping 8.8.8.8

# 5. Common fixes:
# - Move workspaces to SSD
# - Increase container resources
# - Use faster LLM model
# - Clean up old projects
```

### Issue: File operations are slow

**Symptom:**
Reading/writing files takes > 1 second.

**Solution:**
```bash
# Check if workspace is on slow storage
df -h workspaces/

# Move to SSD if on HDD
# Or use ramdisk for temp files (advanced)

# Check Docker volume performance
# On macOS, Docker volumes can be slow
# Consider using :delegated flag (Docker Desktop setting)

# Check for antivirus scanning
# Add workspace directory to exclusions
```

### Issue: High CPU usage

**Symptom:**
CPU constantly at 100%.

**Solution:**
```bash
# Find what's using CPU
htop

# Or
ps aux --sort=-%cpu | head

# Common causes:
# 1. Too many active sessions - reduce MAX_SESSIONS
# 2. Infinite loop in agent - restart session
# 3. Docker container stuck - docker kill <container>

# Limit CPU per container
CONTAINER_CPU_LIMIT=0.5  # Instead of 1.0
```

---

## Project Issues

### Issue: "Project not found" when resuming

**Symptom:**
```
> /resume my-project-xxx
Error: Project not found
```

**Solution:**
```bash
# List available projects
curl http://localhost:8000/api/sessions/resumable

# Or in TUI
> /list

# Check if project was deleted (auto-cleanup)
# Or if you're using wrong ID
# Project IDs are format: prefix-uuid (e.g., my-api-a7b3c2d1)
```

### Issue: Memories not restored

**Symptom:**
Agent doesn't remember previous context after resume.

**Solution:**
```bash
# Check if memory snapshots exist
ls workspaces/*/memories/persistent/

# Check if memory persistence is enabled
grep MEMORY_SNAPSHOT_ENABLED .env
# Should be: true

# Check server logs for snapshot errors
grep -i "memory" server.log

# Manual memory restore (if needed)
# Project will still work, just without historical context
```

### Issue: Project cleanup warnings

**Symptom:**
Logs show: "Project pending cleanup in 3 days"

**Solution:**
```bash
# Pin the project to prevent deletion
curl -X POST http://localhost:8000/api/projects/PROJECT-ID/extend \
  -H "Content-Type: application/json" \
  -d '{"pin": true}'

# Or access the project (resets 30-day timer)
> /resume PROJECT-ID

# Or extend the lifetime
curl -X POST http://localhost:8000/api/projects/PROJECT-ID/extend \
  -H "Content-Type: application/json" \
  -d '{"days": 30}'

# Disable auto-cleanup (not recommended for production)
PROJECT_CLEANUP_ENABLED=false
```

### Issue: Too many projects

**Symptom:**
Disk space filling up with old projects.

**Solution:**
```bash
# List all projects
curl http://localhost:8000/api/projects

# Delete old projects
curl -X DELETE http://localhost:8000/api/projects/PROJECT-ID

# Clean up all unpinned projects older than 30 days
# (This happens automatically if cleanup is enabled)

# Or manually clean workspace directory
rm -rf workspaces/old-project-*

# Adjust cleanup settings
PROJECT_CLEANUP_AFTER_DAYS=7  # Cleanup after 7 days instead of 30
```

---

## Getting Help

### Check Logs

```bash
# Server logs
cd server && uv run uvicorn app.main:app --port 8000 2>&1 | tee server.log

# Or with Docker
docker-compose logs -f cognition

# TUI/client logs
# Usually shown in the TUI interface

# System logs
journalctl -u cognition -f
```

### Debug Mode

Enable debug logging:

```bash
# In .env
LOG_LEVEL=debug
DEBUG=true

# Restart server
# Now you'll see detailed logs
```

### Health Check

```bash
# Check server health
curl http://localhost:8000/health

# Expected output:
{
  "status": "healthy",
  "version": "0.1.0",
  "sessions_active": 0,
  "llm": {
    "configured": true,
    "provider": "openai"
  }
}

# Check specific components
curl http://localhost:8000/api/projects  # List projects
curl http://localhost:8000/api/sessions/resumable  # List resumable
```

### Common Diagnostic Commands

```bash
# Check everything is running
make check-health

# Or manually:
echo "=== Docker ==="
docker ps
docker images | grep opencode

echo "=== Server ==="
curl -s http://localhost:8000/health | jq

echo "=== Workspaces ==="
ls -la workspaces/ | head -10

echo "=== Environment ==="
grep -v '^#' .env | grep -v '^$'

echo "=== Tests ==="
uv run pytest tests/ -q --tb=no
```

### Report an Issue

When reporting issues, include:

1. **Error message** (copy-paste exact error)
2. **Steps to reproduce**
3. **Environment info:**
   ```bash
   # Run this and include output
   uname -a
   docker --version
   python3 --version
   uv --version
   ```
4. **Recent logs** (last 50 lines)
5. **Configuration** (sanitized .env)

### Reset Everything

If all else fails:

```bash
# WARNING: This will delete all data!

# Stop everything
docker-compose down
docker stop $(docker ps -aq)
docker rm $(docker ps -aq)

# Clean up
docker system prune -f
rm -rf workspaces/
rm -rf .venv/

# Reinstall
uv pip install -e ".[all]"
make build-agent-image

# Start fresh
make dev-server
# In another terminal:
make dev-client
```

---

## Quick Reference

| Issue | Quick Fix |
|-------|-----------|
| Server won't start | Check `.env`, restart Docker, check port 8000 |
| Can't connect | Verify server running: `curl localhost:8000/health` |
| No session | Run `/create my-project` first |
| LLM errors | Verify API key: `echo $OPENAI_API_KEY` |
| Container fails | Rebuild: `make build-agent-image` |
| Slow performance | Check `htop`, use faster LLM, move to SSD |
| Project missing | Check `/list` for correct ID |
| Disk full | Delete old projects: `rm -rf workspaces/old-*` |

---

**Still stuck?** Check the logs, verify the health endpoint, and ensure all prerequisites are met.
