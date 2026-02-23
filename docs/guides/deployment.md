# Operations Manual: Deployment

> **Running the Engine in Production.**

This guide covers configuration, security, and scaling for the Cognition Engine.

## Configuration

Cognition is configured via Environment Variables.

### Core Settings

| Variable | Description | Default |
| :--- | :--- | :--- |
| `COGNITION_PORT` | HTTP Port for API | `8000` |
| `COGNITION_LOG_LEVEL` | Logging verbosity | `INFO` |
| `COGNITION_WORKSPACE_ROOT` | Root path for Sandbox files | `./workspaces` |

### LLM Provider

| Variable | Description | Example |
| :--- | :--- | :--- |
| `COGNITION_LLM_PROVIDER` | `openai`, `bedrock`, `openai_compatible` | `openai` |
| `COGNITION_LLM_MODEL` | Model ID | `gpt-4o` |
| `OPENAI_API_KEY` | Key for OpenAI | `sk-...` |

### Agent behavior

| Variable | Description | Default |
| :--- | :--- | :--- |
| `COGNITION_AGENT_MEMORY` | JSON list of files to load as context | `["AGENTS.md"]` |
| `COGNITION_AGENT_SKILLS` | JSON list of skill directory paths | `[".cognition/skills/"]` |
| `COGNITION_AGENT_INTERRUPT_ON` | JSON dict of tools requiring approval | `{}` |

### Persistence (State)

| Variable | Description | Default |
| :--- | :--- | :--- |
| `COGNITION_PERSISTENCE_BACKEND` | `sqlite` or `postgres` | `sqlite` |
| `COGNITION_PERSISTENCE_URI` | Path/URL to DB | `.cognition/state.db` |

> **Postgres is fully implemented** via `server/app/storage/postgres.py` using `asyncpg`.
> Set `COGNITION_PERSISTENCE_BACKEND=postgres` and provide a connection URI
> (e.g., `postgresql://user:pass@localhost:5432/cognition`).

### Sandbox Backend

| Variable | Description | Default |
| :--- | :--- | :--- |
| `COGNITION_SANDBOX_BACKEND` | `local` or `docker` | `local` |

The sandbox backend controls how agent commands are executed. The agent factory
calls `create_sandbox_backend()` which dispatches based on this setting:

- **`local`** — Runs commands directly on the host via `LocalExecutionBackend`.
- **`docker`** — Runs commands in isolated containers via `DockerExecutionBackend`
  (which wraps `CognitionDockerSandboxBackend`). Both classes are fully implemented.

### Observability (Audit)

| Variable | Description | Default |
| :--- | :--- | :--- |
| `COGNITION_OTEL_ENDPOINT` | OTLP Collector URL | `http://otel-collector:4317` |

## Security Hardening

### 0. Docker Sandbox Container Hardening

When `COGNITION_SANDBOX_BACKEND=docker`, every agent command runs inside a
hardened container with the following defaults:

- **`cap_drop=ALL`** — All Linux capabilities are dropped.
- **`security_opt=no-new-privileges`** — Prevents privilege escalation.
- **`read_only=True`** — Root filesystem is read-only.
- **tmpfs mounts** for `/tmp` and `/home` — Writable scratch space that never
  persists to disk.

These settings are applied automatically by `DockerExecutionBackend` and require
no additional configuration.

### 1. File System Isolation (Volumes)
In Docker, always mount the workspace volume with the least privilege required.

**Read-Only Evidence (Forensic Use Case):**
```yaml
volumes:
  - /mnt/evidence:/workspace/evidence:ro
  - /mnt/scratch:/workspace/scratch:rw
```
This ensures the agent can read the evidence but only write to the scratchpad.

### 2. User Permissions
The container runs as user `cognition` (UID 1000). Ensure your host volumes are owned by UID 1000.

```bash
chown -R 1000:1000 /mnt/evidence
```

### 3. Network Egress
For maximum security (e.g., Malware Analysis), disable outbound network access for the container.

```yaml
services:
  cognition:
    # ...
    networks:
      - internal_only
```

## Deployment Modes

### Local Development (Recommended for Security)

Cognition runs directly on the host while supporting services run in Docker Compose:

```bash
# Start supporting services
docker compose up -d postgres mlflow otel-collector

# Run Cognition on the host
COGNITION_SANDBOX_BACKEND=docker uv run uvicorn server.app.main:app --reload --port 8000
```

With `COGNITION_SANDBOX_BACKEND=docker`, every agent command executes inside an
isolated, hardened container (cap_drop=ALL, no-new-privileges, read-only root
filesystem). This is the recommended mode for any environment where the agent
processes untrusted input.

### Docker Compose (Testing / CI)

All services — including Cognition itself — run inside Docker Compose:

```bash
docker compose --profile full up -d
```

In this mode Cognition typically uses `COGNITION_SANDBOX_BACKEND=local`, meaning
agent commands run directly inside the Cognition container with **no additional
isolation**. This is suitable for rapid testing and CI pipelines only; do not use
it for untrusted workloads.

## Scaling

### Stateless Architecture
The Cognition API container is **stateless**. State lives in the Persistence Backend (SQLite/Postgres).

To scale horizontally:
1.  Switch `COGNITION_PERSISTENCE_BACKEND` to `postgres`.
2.  Deploy multiple replicas of the `cognition` container behind a Load Balancer (Nginx/ALB).
3.  Ensure all replicas mount the same Workspace Volume (via NFS/EFS) OR use `DockerExecutionBackend` (`COGNITION_SANDBOX_BACKEND=docker`) which manages its own per-session volumes.

## Health Checks

The engine exposes standard probes for Kubernetes.

*   **Liveness:** `GET /health` (Is the process running?)
*   **Readiness:** `GET /ready` (Can I connect to the LLM and DB?)
