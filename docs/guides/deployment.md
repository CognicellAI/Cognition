# Deployment Guide

This guide covers running Cognition in production with PostgreSQL persistence, Docker sandbox execution, and the full observability stack.

---

## Overview

The production topology includes 8 services:

| Service | Image | Purpose |
|---|---|---|
| `cognition` | `cognition:latest` | The API server |
| `postgres` | `postgres:16` | Durable session and message storage |
| `mlflow` | `ghcr.io/mlflow/mlflow:v3.10.0` | Experiment tracking |
| `prometheus` | `prom/prometheus:latest` | Metrics collection |
| `grafana` | `grafana/grafana:latest` | Dashboards |
| `otel-collector` | `otel/opentelemetry-collector-contrib:latest` | Trace collection and routing |
| `loki` | `grafana/loki:latest` | Log aggregation |
| `promtail` | `grafana/promtail:latest` | Log shipping from containers |

All services communicate on a `cognition-network` bridge network.

---

## Prerequisites

- Docker Engine 24+ and Docker Compose v2
- At least 4 GB free RAM for the full stack (2 GB minimum for Cognition + Postgres only)
- An LLM provider API key

---

## Step 1 — Build the Sandbox Image

The sandbox image is required when `COGNITION_SANDBOX_BACKEND=docker`. It defines the execution environment for agent code.

```bash
docker build -f Dockerfile.sandbox -t cognition-sandbox:latest .
```

The sandbox image is minimal by design: a read-only root filesystem, no shell, no network tools, and only the packages needed to run Python code.

---

## Step 2 — Build the Cognition Image

```bash
docker build -t cognition:latest .
```

The `Dockerfile` is a multi-stage build. The final image contains only the application and its runtime dependencies.

---

## Step 3 — Configure Environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Minimum required settings:

```bash
# LLM provider (set in .cognition/config.yaml or via API)
# Config.yaml llm: section is seeded into ConfigRegistry on first startup
OPENAI_API_KEY=sk-...

# Database (matches docker-compose.yml service)
COGNITION_PERSISTENCE_BACKEND=postgres
COGNITION_PERSISTENCE_URI=postgresql://cognition:cognition@postgres:5432/cognition
POSTGRES_USER=cognition
POSTGRES_PASSWORD=cognition
POSTGRES_DB=cognition

# Sandbox
COGNITION_SANDBOX_BACKEND=docker

# Observability (optional but recommended)
COGNITION_OTEL_ENABLED=true
COGNITION_OTEL_ENDPOINT=http://otel-collector:4317
COGNITION_MLFLOW_ENABLED=true
COGNITION_MLFLOW_TRACKING_URI=http://mlflow:5000
```

---

## Step 4 — Start the Stack

### Full Stack (all 8 services)

```bash
docker-compose up -d
```

### Minimal Stack (Cognition + Postgres only)

```bash
docker-compose up -d cognition postgres
```

### Verify Health

```bash
# Cognition API
curl -s http://localhost:8000/health | jq .

# PostgreSQL
docker-compose exec postgres pg_isready -U cognition

# MLflow
curl -s http://localhost:5000/health

# Prometheus
curl -s http://localhost:9090/-/ready

# Grafana
curl -s http://localhost:3000/api/health
```

---

## Step 5 — Database Migrations

Cognition uses Alembic for schema management. Migrations run automatically at startup — the `SqliteStorageBackend` and `PostgresStorageBackend` both call `metadata.create_all()` during `initialize()`.

For explicit migration management:

```bash
# Apply latest schema
docker-compose exec cognition cognition db upgrade

# Check current revision
docker-compose exec cognition cognition db current

# Create a new migration (after changing schema.py)
docker-compose exec cognition cognition db migrate "description"
```

---

## Service Configuration Details

### Cognition Server

The `cognition` service in `docker-compose.yml` mounts:
- `/var/run/docker.sock` — Required for the Docker sandbox backend to create containers
- `./workspace` — Host workspace directory mapped into the container

The Docker-in-Docker socket mount requires that the host's Docker daemon is accessible and that the `cognition` user has permission to use it.

### PostgreSQL

```yaml
postgres:
  image: postgres:16
  environment:
    POSTGRES_USER: cognition
    POSTGRES_PASSWORD: cognition
    POSTGRES_DB: cognition
  volumes:
    - pgdata:/var/lib/postgresql/data
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U cognition"]
    interval: 10s
    retries: 5
```

Data is persisted in the named `pgdata` volume. Back up this volume before upgrades.

### OTel Collector

The collector receives OTLP gRPC on port 4317, processes traces, and exports them to:
- MLflow (via OTLP HTTP)
- Loki (logs via the `loki` exporter)

Configuration: `docker/otel-collector-config.yml`.

### Grafana

Pre-built dashboards are provisioned automatically from `docker/grafana/dashboards/`. The Grafana admin UI is available at `http://localhost:3000` (default credentials: `admin`/`admin`).

---

## Production Hardening

### Network Isolation

Sandbox containers run with `--network none` by default, preventing agents from accessing the internet or internal services:

```env
COGNITION_DOCKER_NETWORK=none
```

If agents need internet access (e.g. for web search), create a dedicated restricted network instead of using `bridge`:

```bash
docker network create --driver bridge --opt com.docker.network.bridge.name=agent-net \
  --subnet 172.20.0.0/24 agent-restricted
```

```env
COGNITION_DOCKER_NETWORK=agent-restricted
```

### Resource Limits

Prevent runaway agent workloads from starving other services:

```env
COGNITION_DOCKER_MEMORY_LIMIT=1g
COGNITION_DOCKER_CPU_LIMIT=2.0
COGNITION_DOCKER_TIMEOUT=300
```

### Session Scoping

Enable multi-tenant isolation:

```env
COGNITION_SCOPING_ENABLED=true
COGNITION_SCOPE_KEYS=["user", "project"]
```

Your upstream API gateway or reverse proxy must inject the `X-Cognition-Scope-User` and `X-Cognition-Scope-Project` headers based on your authentication layer.

### TLS / Reverse Proxy

Cognition does not terminate TLS. Run it behind a reverse proxy (Nginx, Caddy, AWS ALB) that handles TLS termination.

Nginx example:

```nginx
location / {
    proxy_pass http://cognition:8000;
    proxy_http_version 1.1;
    proxy_set_header Connection "";     # Required for SSE
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_buffering off;                # Required for SSE
    proxy_cache off;                    # Required for SSE
    proxy_read_timeout 300s;            # Long timeout for SSE streams
}
```

**SSE requires `proxy_buffering off`.** Without this, tokens will not stream to clients.

### CORS

Set specific origins in production:

```env
COGNITION_CORS_ORIGINS=["https://app.example.com"]
COGNITION_CORS_ALLOW_CREDENTIALS=true
```

### Secret Management

Never put API keys in YAML config files. Use:
- `.env` files (for Docker Compose; excluded from version control via `.gitignore`)
- Docker secrets for Swarm deployments
- AWS Secrets Manager / HashiCorp Vault for Kubernetes

---

## Kubernetes

### Deployment

The Cognition container is stateless (all state in PostgreSQL). Use a standard `Deployment` with horizontal scaling:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cognition
spec:
  replicas: 3
  template:
    spec:
      containers:
        - name: cognition
          image: cognition:latest
          ports:
            - containerPort: 8000
            - containerPort: 9090   # Prometheus metrics
          env:
            - name: COGNITION_PERSISTENCE_BACKEND
              value: postgres
            - name: COGNITION_PERSISTENCE_URI
              valueFrom:
                secretKeyRef:
                  name: cognition-secrets
                  key: database-url
```

### Kubernetes Sandbox Backend

When deploying Cognition on Kubernetes, the Docker sandbox backend does not work (the server pod runs with `readOnlyRootFilesystem: true`, `capabilities.drop: ["ALL"]`, and `runAsNonRoot: true`). Use the `kubernetes` sandbox backend instead, which creates isolated sandbox pods via the [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) CRD and controller.

**Prerequisites** (install before deploying Cognition):

| Prerequisite | Install | Purpose |
|---|---|---|
| agent-sandbox controller (v0.3.10+) | `kubectl apply -f .../v0.3.10/manifest.yaml` | Reconciles Sandbox CRs into pods |
| agent-sandbox extensions | `kubectl apply -f .../v0.3.10/extensions.yaml` | SandboxTemplate, SandboxClaim CRDs |
| sandbox-router Deployment + Service | Deploy from [agent-sandbox router](https://github.com/kubernetes-sigs/agent-sandbox) | Proxies commands to sandbox pods |

These are cluster-scoped infrastructure and are **not** bundled in Cognition's Helm chart.

**SandboxTemplate** — create a CR defining the sandbox pod spec:

```yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxTemplate
metadata:
  name: cognition-sandbox
  namespace: cognition
spec:
  networkPolicyManagement: Managed
  podTemplate:
    spec:
      containers:
      - name: python-runtime
        image: us-central1-docker.pkg.dev/k8s-staging-images/agent-sandbox/python-runtime-sandbox:latest-main
        ports:
        - containerPort: 8888
        securityContext:
          readOnlyRootFilesystem: true
          runAsNonRoot: true
          capabilities:
            drop: ["ALL"]
        volumeMounts:
        - name: tmp
          mountPath: /tmp
        - name: workspace
          mountPath: /workspace
      volumes:
      - name: tmp
        emptyDir:
          sizeLimit: "128Mi"
      - name: workspace
        emptyDir:
          sizeLimit: "1Gi"
```

> The `/tmp` and `/workspace` emptyDir mounts are required. The runtime image uses `readOnlyRootFilesystem: true`, so without writable mount points, file operations that write temporary data will fail.

**Helm values** — enable the K8s sandbox backend:

```yaml
config:
  sandbox:
    backend: kubernetes
    k8s:
      template: cognition-sandbox
      namespace: cognition
      routerUrl: http://sandbox-router-svc.cognition.svc.cluster.local:8080
      ttl: 3600
      denyEgress: true    # Optional: deny all egress from sandbox pods
```

The Helm chart automatically creates the required RBAC (namespace-scoped Role for sandbox lifecycle + cluster-scoped ClusterRole for CRD reads) when `backend=kubernetes`.

**Startup validation** — Cognition checks at startup that the agent-sandbox CRDs exist and the router is reachable. If CRDs are missing, the server fails to start with a clear error message.

See [Kubernetes Sandbox](../concepts/kubernetes-sandbox.md) for architecture details, scoping labels, and the two-package design.

### Health Probes

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 30

readinessProbe:
  httpGet:
    path: /ready
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 10
```

---

## Monitoring

### Prometheus Scrape Config

```yaml
scrape_configs:
  - job_name: cognition
    static_configs:
      - targets: ["cognition:9090"]
    scrape_interval: 15s
```

### Key Metrics to Alert On

| Metric | Alert Condition | Description |
|---|---|---|
| `cognition_requests_total{status=~"5.."}` | Rate > 0 sustained | Server-side errors |
| `cognition_llm_call_duration_seconds` | p99 > 30s | LLM latency degradation |
| `cognition_tool_calls_total{status="error"}` | Rate spike | Tool execution failures |
| `cognition_active_sessions` | Near `COGNITION_MAX_SESSIONS` | Session limit approaching |

---

## Upgrading

1. Pull the new image: `docker pull cognition:latest`
2. Run migrations: `docker-compose exec cognition cognition db upgrade`
3. Rolling restart: `docker-compose up -d --no-deps cognition`

The `StorageBackend.initialize()` call at startup is idempotent — it is safe to run against an existing database.
