# Cognition — Part 2: Multi-Tenancy on Kubernetes

## Overview

This document describes how Cognition scales from a single-user local tool to a multi-tenant PaaS on Kubernetes. The core agent code does not change. The only difference is the sandbox backend and the orchestration layer around it.

## Architecture

```
                         ┌─────────────────────────────┐
                         │        Ingress Controller     │
                         │   tenant-a.cognition.dev     │
                         │   tenant-b.cognition.dev     │
                         └──────────┬──────────────────┘
                                    │
                         ┌──────────▼──────────────────┐
                         │    Cognition Operator        │
                         │    (control plane)           │
                         │                              │
                         │  Watches: CognitionTenant CR │
                         │  Creates: namespace, server, │
                         │    RBAC, NetworkPolicy,      │
                         │    ResourceQuota, PVC        │
                         └──────────┬──────────────────┘
                                    │ reconciles
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
         ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
         │ ns: tenant-a │ │ ns: tenant-b │ │ ns: tenant-c │
         │              │ │              │ │              │
         │ ┌──────────┐ │ │ ┌──────────┐ │ │ ┌──────────┐ │
         │ │  Server  │ │ │ │  Server  │ │ │ │  Server  │ │
         │ │ (FastAPI) │ │ │ │ (FastAPI) │ │ │ │ (FastAPI) │ │
         │ └────┬─────┘ │ │ └────┬─────┘ │ │ └────┬─────┘ │
         │      │       │ │      │       │ │      │       │
         │ ┌────▼─────┐ │ │ ┌────▼─────┐ │ │ ┌────▼─────┐ │
         │ │ Sandbox  │ │ │ │ Sandbox  │ │ │ │ Sandbox  │ │
         │ │  Pods    │ │ │ │  Pods    │ │ │ │  Pods    │ │
         │ └──────────┘ │ │ └──────────┘ │ │ └──────────┘ │
         │              │ │              │ │              │
         │ ┌──────────┐ │ │ ┌──────────┐ │ │ ┌──────────┐ │
         │ │   PVC    │ │ │ │   PVC    │ │ │ │   PVC    │ │
         │ │(workspace)│ │ │(workspace)│ │ │ │(workspace)│ │
         │ └──────────┘ │ │ └──────────┘ │ │ └──────────┘ │
         └──────────────┘ └──────────────┘ └──────────────┘

Shared Services (cognition-system namespace):
  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
  │ Postgres │  │Prometheus│  │  Grafana  │  │  Redis   │
  │(checkpts)│  │(metrics) │  │(dashboards│  │ (store)  │
  └──────────┘  └──────────┘  └──────────┘  └──────────┘
```

## Tenant Model

### Custom Resource Definition

A tenant is declared as a Kubernetes custom resource:

```yaml
apiVersion: cognition.dev/v1
kind: CognitionTenant
metadata:
  name: tenant-a
  namespace: cognition-system
spec:
  # Identity
  displayName: "Acme Corp"
  adminEmail: "admin@acme.com"

  # Resource limits for the entire tenant
  quotas:
    maxSessions: 10
    maxCpuPerSandbox: "2"
    maxMemoryPerSandbox: "4Gi"
    maxStorageGb: 50
    maxSandboxDurationMinutes: 60

  # LLM configuration
  llm:
    provider: "openai"          # or "bedrock"
    model: "gpt-4o"
    # Secret reference for API key (never inline)
    apiKeySecretRef:
      name: tenant-a-llm-keys
      key: OPENAI_API_KEY

  # Sandbox image
  sandbox:
    image: "cognition-sandbox:latest"
    pullPolicy: IfNotPresent

  # Network policy
  network:
    egressAllowed: false        # Block outbound by default
    allowedEgressCIDRs: []      # Whitelist if needed
```

### What the Operator Provisions Per Tenant

When a `CognitionTenant` CR is created, the operator creates:

| Resource | Name | Purpose |
|----------|------|---------|
| `Namespace` | `cognition-tenant-{name}` | Isolation boundary |
| `ResourceQuota` | `tenant-quota` | CPU/memory/pod limits |
| `LimitRange` | `tenant-limits` | Default container limits |
| `NetworkPolicy` | `deny-all-egress` | Block outbound traffic |
| `NetworkPolicy` | `allow-server-to-sandbox` | Server ↔ sandbox communication |
| `NetworkPolicy` | `allow-ingress` | External traffic to server only |
| `ServiceAccount` | `cognition-server` | Server pod identity |
| `Role` | `sandbox-manager` | Permission to create/delete sandbox pods |
| `RoleBinding` | `server-sandbox-manager` | Binds role to server SA |
| `Deployment` | `cognition-server` | FastAPI server (1 replica) |
| `Service` | `cognition-server` | ClusterIP for server |
| `PVC` | `tenant-workspace` | Persistent storage for project files |
| `Secret` | (copied from ref) | LLM API keys |

## Operator Architecture

### Technology

Python-based operator using [kopf](https://kopf.readthedocs.io/) (Kubernetes Operator Pythonic Framework). Chosen because:
- Same language as the rest of Cognition
- Lightweight (no code generation, no Java)
- Declarative handler registration
- Built-in retry, leader election, finalizers

### Controller Reconciliation Loop

```python
import kopf
import kubernetes

@kopf.on.create('cognition.dev', 'v1', 'cognitiontenants')
async def on_tenant_create(spec, name, namespace, **kwargs):
    tenant_ns = f"cognition-tenant-{name}"

    # 1. Create namespace
    create_namespace(tenant_ns, labels={"cognition.dev/tenant": name})

    # 2. Apply resource quota
    apply_resource_quota(tenant_ns, spec["quotas"])

    # 3. Apply network policies
    apply_network_policies(tenant_ns, spec["network"])

    # 4. Create RBAC
    apply_rbac(tenant_ns)

    # 5. Copy LLM secret into tenant namespace
    copy_secret(
        source_ns=namespace,
        source_name=spec["llm"]["apiKeySecretRef"]["name"],
        target_ns=tenant_ns,
    )

    # 6. Deploy server
    apply_server_deployment(tenant_ns, spec)

    # 7. Create workspace PVC
    apply_workspace_pvc(tenant_ns, spec["quotas"]["maxStorageGb"])


@kopf.on.delete('cognition.dev', 'v1', 'cognitiontenants')
async def on_tenant_delete(name, **kwargs):
    tenant_ns = f"cognition-tenant-{name}"
    # Delete namespace (cascades all resources)
    delete_namespace(tenant_ns)


@kopf.on.update('cognition.dev', 'v1', 'cognitiontenants')
async def on_tenant_update(spec, name, diff, **kwargs):
    tenant_ns = f"cognition-tenant-{name}"
    # Reconcile changed fields (quotas, LLM config, etc.)
    for field, old_val, new_val in diff:
        if "quotas" in field:
            apply_resource_quota(tenant_ns, spec["quotas"])
        if "llm" in field:
            restart_server(tenant_ns)
```

### Operator Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cognition-operator
  namespace: cognition-system
spec:
  replicas: 1
  template:
    spec:
      serviceAccountName: cognition-operator
      containers:
        - name: operator
          image: cognition-operator:latest
          env:
            - name: OPERATOR_NAMESPACE
              value: cognition-system
```

The operator's service account has cluster-wide permissions to create namespaces, deployments, RBAC, etc.

## Isolation Boundaries

### Layer 1: Namespace

Each tenant runs in its own namespace. Namespaces provide:
- Resource scoping (pods, services, secrets are namespace-local)
- RBAC boundary (roles are namespace-scoped)
- Network policy scope
- Resource quota enforcement

### Layer 2: RBAC

```yaml
# Server can only manage pods in its own namespace
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: sandbox-manager
  namespace: cognition-tenant-{name}
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/exec"]
    verbs: ["create", "delete", "get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]
```

The server cannot access pods in other tenant namespaces. The sandbox pods have no service account (no K8s API access).

### Layer 3: NetworkPolicy

```yaml
# Default: deny all egress from sandbox pods
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: sandbox-deny-egress
  namespace: cognition-tenant-{name}
spec:
  podSelector:
    matchLabels:
      cognition.dev/role: sandbox
  policyTypes: ["Egress"]
  egress: []  # Nothing allowed

---
# Allow server to reach sandbox pods
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: server-to-sandbox
  namespace: cognition-tenant-{name}
spec:
  podSelector:
    matchLabels:
      cognition.dev/role: sandbox
  policyTypes: ["Ingress"]
  ingress:
    - from:
        - podSelector:
            matchLabels:
              cognition.dev/role: server
```

### Layer 4: ResourceQuota

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: tenant-quota
  namespace: cognition-tenant-{name}
spec:
  hard:
    pods: "12"                    # 1 server + up to 10 sandboxes + 1 buffer
    requests.cpu: "20"
    requests.memory: "40Gi"
    limits.cpu: "20"
    limits.memory: "40Gi"
    persistentvolumeclaims: "2"
    requests.storage: "50Gi"
```

### Layer 5: Pod Security (seccomp)

```yaml
# Sandbox pod template
spec:
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    fsGroup: 1000
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: sandbox
      securityContext:
        allowPrivilegeEscalation: false
        capabilities:
          drop: ["ALL"]
        readOnlyRootFilesystem: false  # Agent needs to write files
```

## Sandbox Pod Lifecycle

When a user creates a session, the server creates a sandbox pod in the tenant namespace:

```
User creates session
    │
    ▼
Server (in tenant namespace)
    │  kubernetes.client.CoreV1Api().create_namespaced_pod()
    ▼
Sandbox Pod created
    │  image: cognition-sandbox:latest
    │  resources: {cpu: "2", memory: "4Gi"} (from tenant quotas)
    │  volumes: workspace PVC mounted at /workspace
    │  labels: {cognition.dev/role: sandbox, cognition.dev/session: <id>}
    ▼
K8sSandbox(namespace, pod_name) returned to agent factory
    │
    ▼
Agent uses execute() → kubectl exec into sandbox pod
```

### Pod Template

```python
def create_sandbox_pod(namespace: str, session_id: str, spec: dict) -> str:
    pod_name = f"sandbox-{session_id[:8]}"
    pod = kubernetes.client.V1Pod(
        metadata=kubernetes.client.V1ObjectMeta(
            name=pod_name,
            namespace=namespace,
            labels={
                "cognition.dev/role": "sandbox",
                "cognition.dev/session": session_id,
            },
        ),
        spec=kubernetes.client.V1PodSpec(
            restart_policy="Never",
            service_account_name="",  # No K8s API access
            automount_service_account_token=False,
            security_context=kubernetes.client.V1PodSecurityContext(
                run_as_non_root=True,
                run_as_user=1000,
                fs_group=1000,
            ),
            containers=[
                kubernetes.client.V1Container(
                    name="sandbox",
                    image=spec["sandbox"]["image"],
                    command=["sleep", "infinity"],
                    resources=kubernetes.client.V1ResourceRequirements(
                        limits={
                            "cpu": spec["quotas"]["maxCpuPerSandbox"],
                            "memory": spec["quotas"]["maxMemoryPerSandbox"],
                        },
                    ),
                    volume_mounts=[
                        kubernetes.client.V1VolumeMount(
                            name="workspace",
                            mount_path="/workspace",
                            sub_path=session_id,
                        ),
                    ],
                ),
            ],
            volumes=[
                kubernetes.client.V1Volume(
                    name="workspace",
                    persistent_volume_claim=kubernetes.client.V1PersistentVolumeClaimVolumeSource(
                        claim_name="tenant-workspace",
                    ),
                ),
            ],
        ),
    )

    k8s = kubernetes.client.CoreV1Api()
    k8s.create_namespaced_pod(namespace=namespace, body=pod)
    # Wait for pod ready
    wait_for_pod_ready(namespace, pod_name)
    return pod_name
```

### Session Lifecycle

```
Session create  → create sandbox pod → K8sSandbox(ns, pod)
Session active  → agent calls execute() → kubectl exec
Session timeout → delete sandbox pod
Session end     → delete sandbox pod
Server shutdown → delete all sandbox pods in namespace
```

### Timeout Enforcement

The server tracks sandbox pod age. If a session exceeds `maxSandboxDurationMinutes`, the pod is deleted:

```python
async def enforce_timeouts(namespace: str, max_minutes: int):
    while True:
        pods = list_sandbox_pods(namespace)
        for pod in pods:
            age = now() - pod.creation_timestamp
            if age > timedelta(minutes=max_minutes):
                delete_pod(namespace, pod.name)
                notify_session_expired(pod.labels["cognition.dev/session"])
        await asyncio.sleep(60)
```

## Control Plane vs Data Plane

### Control Plane (cognition-system namespace)

Shared across all tenants:

| Component | Purpose |
|-----------|---------|
| Cognition Operator | Watches CRDs, provisions tenant resources |
| PostgreSQL | LangGraph checkpointer (shared, tenant-scoped by thread_id prefix) |
| Redis | LangGraph BaseStore for `/memories/` backend |
| Prometheus | Metrics collection |
| Grafana | Dashboards |
| Ingress Controller | Routes traffic to tenant servers |

### Data Plane (per-tenant namespace)

Isolated per tenant:

| Component | Purpose |
|-----------|---------|
| Cognition Server | FastAPI, handles WebSocket connections, runs Deep Agent |
| Sandbox Pods | Ephemeral, created per session, run agent commands |
| Workspace PVC | Persistent storage for project files |
| LLM Secret | API keys for the tenant's LLM provider |

## Networking

### Ingress Routing

Each tenant gets a subdomain:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: tenant-ingress
  namespace: cognition-tenant-{name}
  annotations:
    nginx.ingress.kubernetes.io/websocket-services: "cognition-server"
spec:
  tls:
    - hosts:
        - "{name}.cognition.dev"
      secretName: cognition-wildcard-tls
  rules:
    - host: "{name}.cognition.dev"
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: cognition-server
                port:
                  number: 8000
```

### Traffic Flow

```
User (TUI/Web)
    │ wss://tenant-a.cognition.dev/ws
    ▼
Ingress Controller (cognition-system)
    │ routes by Host header
    ▼
cognition-server Service (cognition-tenant-tenant-a)
    │ ClusterIP
    ▼
cognition-server Pod
    │ kubectl exec
    ▼
sandbox Pod (same namespace)
```

### DNS

Wildcard DNS record: `*.cognition.dev → Ingress Controller LoadBalancer IP`

## Storage

### Workspace Storage (per tenant)

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: tenant-workspace
  namespace: cognition-tenant-{name}
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 50Gi
  storageClassName: gp3  # Or equivalent
```

Each session gets a subdirectory on the PVC via `subPath`. The server pod mounts the PVC to manage files. Sandbox pods mount only their session's subdirectory.

### Checkpointer Storage (shared)

LangGraph checkpointer uses shared PostgreSQL. Thread IDs are prefixed with tenant name to prevent cross-tenant access:

```python
thread_id = f"tenant-{tenant_name}:{session_id}"
```

### Memory Store (shared)

`StoreBackend` for `/memories/` uses shared Redis. Namespaced by `assistant_id` which includes tenant name:

```python
assistant_id = f"tenant-{tenant_name}"
```

## Scaling

### Horizontal: Server Pods

Each tenant server can scale via HPA if needed:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: cognition-server
  namespace: cognition-tenant-{name}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: cognition-server
  minReplicas: 1
  maxReplicas: 3
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

Note: WebSocket connections are stateful. Scaling beyond 1 replica requires sticky sessions or a shared session store.

### Vertical: Sandbox Pods

Sandbox resource limits are set per tenant via the CRD `quotas` field. The operator enforces these as container resource limits.

### Cluster Level

- Node autoscaler handles adding/removing nodes as tenant count grows
- Sandbox pods are ephemeral and bursty — use spot/preemptible nodes with tolerations
- Server pods should run on on-demand nodes for stability

## Security

### Threat Model

| Threat | Mitigation |
|--------|-----------|
| Sandbox escape | seccomp RuntimeDefault, drop all capabilities, non-root |
| Cross-tenant data access | Namespace isolation, NetworkPolicy, RBAC |
| Secret exfiltration from sandbox | No secrets mounted in sandbox pods. LLM keys only in server pod |
| Network exfiltration | Default-deny egress NetworkPolicy on sandbox pods |
| Resource exhaustion (DoS) | ResourceQuota per namespace, LimitRange per container |
| Lateral movement | No service account in sandbox, automountServiceAccountToken: false |
| Privilege escalation | allowPrivilegeEscalation: false |
| Persistent compromise | Sandbox pods are ephemeral (deleted on session end) |

### Secret Handling

LLM API keys flow:

```
Tenant CRD (secretRef) → Operator copies to tenant namespace → Server pod env var → LLM call
```

Sandbox pods never see API keys. The server makes LLM calls, not the sandbox. The sandbox only runs user code via `execute()`.

### Audit Logging

Enable K8s audit logging for:
- Pod creation/deletion (tracks sandbox lifecycle)
- Pod exec (tracks every command the agent runs)
- Secret access (tracks who reads API keys)

## Migration Path

### From Local to K8s

The agent code and server code are identical. Only the backend and infrastructure change:

| Component | Local | K8s |
|-----------|-------|-----|
| Backend | `LocalSandbox(root_dir=".")` | `K8sSandbox(namespace, pod_name)` |
| Checkpointer | `MemorySaver()` | `AsyncPostgresSaver(DB_URI)` |
| Store | `InMemoryStore()` | Redis-backed `BaseStore` |
| Settings | `.env` file | K8s Secret + env vars |
| Session management | In-memory dict | Same (per server instance) |
| Ingress | `localhost:8000` | `tenant.cognition.dev` |

### Step-by-Step Migration

1. Build and push container images (server, sandbox, operator)
2. Deploy shared services (Postgres, Redis, Prometheus, Ingress)
3. Deploy Cognition Operator
4. Create first tenant CRD
5. Operator provisions namespace + server + RBAC + NetworkPolicy
6. Point DNS wildcard to Ingress
7. Connect TUI to `wss://tenant-a.cognition.dev/ws`

## Operator File Structure

```
operator/
├── Dockerfile
├── pyproject.toml
├── operator/
│   ├── __init__.py
│   ├── main.py              # kopf handlers (create/update/delete)
│   ├── resources.py          # K8s resource builders (namespace, RBAC, etc.)
│   └── templates/
│       ├── server-deployment.yaml
│       ├── network-policy.yaml
│       ├── resource-quota.yaml
│       └── rbac.yaml
└── crds/
    └── cognitiontenant-crd.yaml
```

## Cost Considerations

| Resource | Per Tenant | Notes |
|----------|-----------|-------|
| Server pod | 0.5 CPU, 1Gi RAM (idle) | Always running |
| Sandbox pod | Up to 2 CPU, 4Gi RAM | Only during active sessions |
| Workspace PVC | 50Gi default | Persistent, billed continuously |
| LLM API calls | Pass-through to tenant | Tenant provides their own key |
| Shared Postgres | Amortized | One instance serves all tenants |
| Shared Redis | Amortized | One instance serves all tenants |

For a tenant with 0 active sessions, the cost is: 1 small server pod + PVC storage.

## References

- Part 1 (local architecture): `docs/design.md`
- kopf operator framework: https://kopf.readthedocs.io/
- K8s NetworkPolicy: https://kubernetes.io/docs/concepts/services-networking/network-policies/
- K8s RBAC: https://kubernetes.io/docs/reference/access-authn-authz/rbac/
- K8s ResourceQuota: https://kubernetes.io/docs/concepts/policy/resource-quotas/
- LangGraph PostgresSaver: https://langchain-ai.github.io/langgraph/reference/checkpoints/#postgressaver
- Deep Agents sandboxes: https://docs.langchain.com/oss/python/deepagents/sandboxes
