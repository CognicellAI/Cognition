# Deployment Guide

This guide covers running Cognition in production environments with proper isolation, persistence, and observability.

## Architecture

Cognition typically runs as a containerized service alongside:
1.  **PostgreSQL** (for session state)
2.  **MLflow** (optional, for LLM tracking)
3.  **OpenTelemetry Collector** (optional, for tracing)
4.  **Sandbox Containers** (dynamic, one per session)

## Docker Deployment

### 1. Build Sandbox Image

The sandbox image is required when `COGNITION_SANDBOX_BACKEND=docker`. It defines the environment where agents execute code.

```bash
docker build -f Dockerfile.sandbox -t cognition-sandbox:latest .
```

### 2. Docker Compose Stack

A complete production stack includes the API server, database, and observability tools.

```yaml
version: '3.8'

services:
  cognition:
    image: cognition:latest
    ports:
      - "8000:8000"
    environment:
      - COGNITION_LLM_PROVIDER=openai
      - COGNITION_PERSISTENCE_BACKEND=postgres
      - COGNITION_PERSISTENCE_URI=postgresql://cognition:secret@postgres:5432/cognition
      - COGNITION_SANDBOX_BACKEND=docker
      - COGNITION_DOCKER_HOST_WORKSPACE=${PWD}/workspace
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./workspace:/workspace

  postgres:
    image: postgres:16
    environment:
      - POSTGRES_USER=cognition
      - POSTGRES_PASSWORD=secret
      - POSTGRES_DB=cognition
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

### 3. Security Hardening

When running in production, ensure the following:

-   **Network Isolation**: Set `COGNITION_DOCKER_NETWORK=none` to prevent agents from accessing the internet or internal network unless explicitly allowed.
-   **Resource Limits**: Configure `COGNITION_DOCKER_MEMORY_LIMIT` and `COGNITION_DOCKER_CPU_LIMIT` to prevent DoS.
-   **Read-Only Root**: The sandbox container runs with a read-only root filesystem.
-   **Capabilities**: The sandbox drops all capabilities (`cap_drop: ALL`).

## Database Migrations

Use Alembic to manage the database schema.

```bash
# Initialize
cognition db init

# Upgrade schema
cognition db upgrade

# Create new migration
cognition db migrate "add new table"
```

## Observability

### OpenTelemetry
Configure `COGNITION_OTEL_ENDPOINT` to point to your collector (e.g., Jaeger, SigNoz).
Tracing includes:
-   HTTP request duration
-   LLM call latency and token counts
-   Tool execution timing

### Prometheus Metrics
Scrape metrics from port `9090` (configurable via `COGNITION_METRICS_PORT`).
Key metrics:
-   `llm_call_duration_seconds`
-   `tool_call_total`
-   `active_sessions`

### Logging
Logs are structured JSON by default in production. Use a log aggregator like Loki or CloudWatch.
