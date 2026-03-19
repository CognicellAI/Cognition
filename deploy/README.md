# Cognition Kubernetes Deployment

This directory contains Kubernetes deployment configurations for Cognition AI assistant.

## 📁 Directory Structure

```
deploy/
├── cnpg/
│   └── cluster.yaml              # CNPG PostgreSQL Cluster
├── helm/
│   ├── cognition/                # Main Cognition Helm chart
│   │   ├── Chart.yaml
│   │   ├── values.yaml           # Production values for dubaimetal
│   │   └── templates/
│   └── cognition-observe/        # Observability stack (optional)
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
└── README.md                     # This file
```

## 🚀 Quick Start

### Prerequisites

- Kubernetes cluster with CNPG operator installed
- Helm 3.x installed
- `kubectl` configured for your cluster
- Access to a container registry with Cognition image

### Step 1: Deploy PostgreSQL Cluster

Cognition requires PostgreSQL for persistence. Deploy a CNPG cluster:

```bash
# Deploy the CNPG cluster
kubectl apply -f deploy/cnpg/cluster.yaml

# Wait for the cluster to be ready
kubectl wait --for=condition=Ready cluster/cognition-db -n cognition --timeout=300s

# Verify the cluster is running
kubectl get clusters -n cognition
```

### Step 2: Create Database Credentials Secret

Create a Kubernetes secret with the database connection URI:

```bash
# Get the database password from the init secret
DB_PASSWORD=$(kubectl get secret cognition-db-init-credentials -n cognition -o jsonpath='{.data.password}' | base64 -d)

# Create the credentials secret
kubectl create secret generic cognition-db-credentials \
  --namespace cognition \
  --from-literal=uri="postgresql+asyncpg://cognition:${DB_PASSWORD}@cognition-db-rw:5432/cognition"
```

### Step 3: Deploy Cognition

Deploy the Cognition Helm chart:

```bash
# Navigate to the chart directory
cd deploy/helm/cognition

# Install the chart
helm install cognition . \
  --namespace cognition \
  --create-namespace

# Or with custom values
helm install cognition . \
  --namespace cognition \
  --create-namespace \
  --set secrets.openaiCompatibleApiKey="your-api-key"
```

### Step 4: Configure API Keys

Create a secret with your LLM provider API keys:

```bash
kubectl create secret generic cognition-api-keys \
  --namespace cognition \
  --from-literal=openai-compatible-api-key="your-api-key"
```

Then update the deployment to use it:

```bash
helm upgrade cognition . \
  --namespace cognition \
  --set secrets.openaiCompatibleApiKey="your-api-key"
```

### Step 5: Verify Deployment

```bash
# Check pod status
kubectl get pods -n cognition

# Check logs
kubectl logs -n cognition deployment/cognition

# Test the API (via port-forward)
kubectl port-forward -n cognition svc/cognition 8000:8000
curl http://localhost:8000/health
```

### Step 6: Access via Tailscale

The Cognition service is automatically exposed via Tailscale Ingress:

```bash
# Access via Tailscale hostname
curl http://cognition:8000/health
```

## 📊 Deploy Observability Stack (Optional)

Deploy the full observability stack in a separate namespace:

```bash
cd deploy/helm/cognition-observe

helm install observe . \
  --namespace cognition-observe \
  --create-namespace

# Access Grafana
kubectl port-forward -n cognition-observe svc/grafana 3000:3000
# Open http://localhost:3000 (admin/admin)
```

## ⚙️ Configuration

### Key Values

See `values.yaml` for all configuration options. Key settings:

| Setting | Description | Default |
|---------|-------------|---------|
| `replicaCount` | Number of Cognition replicas | 3 |
| `database.host` | PostgreSQL host | cognition-db-rw |
| `config.llm.provider` | LLM provider | openai_compatible |
| `config.llm.model` | LLM model | google/gemini-3-flash-preview |
| `persistence.workspace.size` | Workspace PVC size | 50Gi |
| `tailscale.enabled` | Enable Tailscale ingress | true |
| `tailscale.hostname` | Tailscale hostname | cognition |

### Custom Values

Create a `values-custom.yaml` file for your environment:

```yaml
replicaCount: 3

config:
  llm:
    provider: openai
    model: gpt-4o
    
  observability:
    enabled: true
    otelEndpoint: http://otel-collector.cognition-observe:4317
    mlflowEnabled: true
    mlflowUri: http://mlflow.cognition-observe:5000

secrets:
  openaiApiKey: "sk-..."
```

Deploy with custom values:

```bash
helm upgrade cognition . \
  --namespace cognition \
  -f values-custom.yaml
```

## 🔧 Maintenance

### Upgrading

```bash
# Update the chart
helm upgrade cognition . --namespace cognition

# Rolling restart
kubectl rollout restart deployment/cognition -n cognition
```

### Scaling

```bash
# Scale replicas
kubectl scale deployment/cognition -n cognition --replicas=5

# Or via Helm
helm upgrade cognition . --namespace cognition --set replicaCount=5
```

### Backup

The CNPG cluster supports automated backups. To enable:

1. Uncomment the ScheduledBackup in `deploy/cnpg/cluster.yaml`
2. Configure S3 or other backup destination
3. Apply the changes

### Troubleshooting

```bash
# Check pod status
kubectl get pods -n cognition -o wide

# View logs
kubectl logs -n cognition deployment/cognition --tail=100 -f

# Describe pod for events
kubectl describe pod -n cognition -l app.kubernetes.io/name=cognition

# Check database connectivity
kubectl exec -it -n cognition deployment/cognition -- \
  python -c "import asyncio; from server.app.storage import get_storage_backend; ..."

# Check PVC usage
kubectl get pvc -n cognition
kubectl describe pvc -n cognition cognition-workspace
```

## 🗑️ Cleanup

```bash
# Remove Cognition
helm uninstall cognition -n cognition

# Remove observability
helm uninstall observe -n cognition-observe

# Remove PostgreSQL
kubectl delete -f deploy/cnpg/cluster.yaml

# Remove namespace (caution: deletes all data!)
kubectl delete namespace cognition
kubectl delete namespace cognition-observe
```

## 📝 Notes

- **PostgreSQL**: This chart does NOT include PostgreSQL. Deploy it separately using the provided CNPG cluster YAML.
- **Storage**: Uses Longhorn storage class by default (supports RWX for workspace sharing).
- **No Hard Limits**: Resource limits are not set by default (as requested). Adjust `resources.requests` in values.yaml if needed.
- **Tailscale**: Automatically configured for external access. Ensure Tailscale operator is running.

## ⚠️ v0.3.0 Breaking Changes

Cognition v0.3.0 introduced **Dynamic ConfigRegistry** which changes how configuration works:

### What Changed
- **Removed**: Environment variables for LLM/agent configuration
  - `COGNITION_LLM_PROVIDER`, `COGNITION_LLM_MODEL`, etc.
  - `COGNITION_OPENAI_*` settings (except API keys)
  - `COGNITION_AGENT_*` settings

- **New**: Configuration via `.cognition/config.yaml` (ConfigMap) or API
  - LLM settings → `config.yaml` (mounted via ConfigMap)
  - Agent settings → `config.yaml` or API
  - Hot-reload supported (no restart needed)

### What Stays as Environment Variables
- Database credentials (`COGNITION_PERSISTENCE_URI`)
- API keys (`COGNITION_OPENAI_COMPATIBLE_API_KEY`, `AWS_*`)
- Observability settings
- Server settings (port, log level)

### Migration
Configuration is now in `values.yaml` under `config:` section and rendered into a ConfigMap:
```yaml
config:
  llm:
    provider: openai_compatible
    model: google/gemini-3-flash-preview
    # base_url is also set here for v0.3.0
```

## 🔗 References

- [Cognition Documentation](../../docs/)
- [CNPG Documentation](https://cloudnative-pg.io/documentation/)
- [Helm Documentation](https://helm.sh/docs/)
