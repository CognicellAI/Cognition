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

### Persistence (State)

| Variable | Description | Default |
| :--- | :--- | :--- |
| `COGNITION_PERSISTENCE_BACKEND` | `sqlite` or `postgres` (future) | `sqlite` |
| `COGNITION_PERSISTENCE_URI` | Path/URL to DB | `.cognition/state.db` |

### Observability (Audit)

| Variable | Description | Default |
| :--- | :--- | :--- |
| `COGNITION_OTEL_ENDPOINT` | OTLP Collector URL | `http://jaeger:4318` |

## Security Hardening

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

## Scaling

### Stateless Architecture
The Cognition API container is **stateless**. State lives in the Persistence Backend (SQLite/Postgres).

To scale horizontally:
1.  Switch `COGNITION_PERSISTENCE_BACKEND` to `postgres`.
2.  Deploy multiple replicas of the `cognition` container behind a Load Balancer (Nginx/ALB).
3.  Ensure all replicas mount the same Workspace Volume (via NFS/EFS) OR use `CognitionDockerSandboxBackend` (future) which manages its own volumes.

## Health Checks

The engine exposes standard probes for Kubernetes.

*   **Liveness:** `GET /health` (Is the process running?)
*   **Readiness:** `GET /ready` (Can I connect to the LLM and DB?)
