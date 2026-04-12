# Kubernetes Sandbox Backend

How Cognition runs agent commands in Kubernetes-native sandboxes using the [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) CRD and controller.

---

## Why a K8s Backend

Cognition ships three sandbox backends:

| Backend | Isolation | Works on K8s? |
|---|---|---|
| `local` | None — commands run as server process user | Yes, but no isolation |
| `docker` | Container per session | No — requires Docker socket + privileged mode |
| `kubernetes` | Sandbox pod per session | Yes — K8s-native, no special privileges needed |

The Cognition Helm chart deploys the server with `readOnlyRootFilesystem: true`, `capabilities.drop: ["ALL"]`, and `runAsNonRoot: true`. The Docker backend cannot work under these constraints. The K8s backend uses the agent-sandbox CRD + controller + router to provide isolated sandbox pods without requiring any privileged access from the Cognition server.

---

## Two-Package Split

```
┌─────────────────────────────────────────────────────┐
│  Cognition (this repo)                              │
│                                                     │
│  CognitionKubernetesSandboxBackend                  │
│  • Protected path enforcement (.cognition/)         │
│  • Scoping labels from CognitionContext             │
│  • Session-scoped lifecycle                         │
│  • Delegates all execution to K8sSandbox            │
│                                                     │
│  Wraps ──────────────────────────────────┐          │
│                                         │          │
└─────────────────────────────────────────┼──────────┘
                                          │
┌─────────────────────────────────────────┼──────────┐
│  langchain-k8s-sandbox (standalone pkg) │          │
│                                         ▼          │
│  K8sSandbox(BaseSandbox)                          │
│  • Lazy init on first execute()                   │
│  • Labels passthrough to Sandbox CR               │
│  • TTL via spec.shutdownTime patch                │
│  • BaseSandbox file ops via execute()             │
│                                                   │
│  Uses ─────────────────────────────────┐          │
│                                       │          │
└───────────────────────────────────────┼──────────┘
                                      │
┌──────────────────────────────────────┼────────────┐
│  k8s-agent-sandbox SDK (PyPI)       │            │
│                                     ▼            │
│  SandboxClient                                   │
│  • create_sandbox(template, namespace, labels)   │
│  • sandbox.commands.run(cmd, timeout)            │
│  • sandbox.terminate()                           │
│  • SandboxDirectConnectionConfig(router_url)     │
└──────────────────────────────────────────────────┘
```

The split follows the `langchain-<provider>` convention. `langchain-k8s-sandbox` is published as a standalone package with zero Cognition imports. Cognition wraps it with domain policy (protected paths, scoping labels, session lifecycle).

Design doc for the standalone package: [`packages/langchain-k8s-sandbox/DESIGN.md`](../../packages/langchain-k8s-sandbox/DESIGN.md)

---

## Execution Flow

```
User sends message
        │
        ▼
┌──────────────────┐
│  Cognition API   │  POST /sessions/{id}/messages
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│  CognitionAgent  │  LangGraph ReAct loop
│  (runtime)       │  decides to call a shell tool
└──────┬───────────┘
       │
       ▼
┌──────────────────────────────────────────┐
│  CognitionKubernetesSandboxBackend       │
│  ┌────────────────────────────────────┐  │
│  │ Protected path guard (write/edit)  │  │
│  └──────────────┬─────────────────────┘  │
│                 ▼                         │
│  K8sSandbox.execute()                     │
│  (lazy creates Sandbox CR on first call) │
└─────────────────┬────────────────────────┘
                  │
    ┌─────────────┴─────────────┐
    ▼ (first call)              ▼ (subsequent calls)
┌──────────────────┐    ┌──────────────────┐
│ SDK:             │    │ SDK:             │
│ create_sandbox() │    │ commands.run()   │
│ + patch TTL      │    │                  │
└──────┬───────────┘    └────────┬─────────┘
       │                         │
       ▼                         │
┌──────────────────┐             │
│ K8s API Server   │             │
│ → SandboxClaim   │             │
│ → controller     │             │
│ → Sandbox CR     │             │
│ → Pod spawned    │             │
└──────────────────┘             │
                                 │
       ┌─────────────────────────┘
       ▼
┌──────────────────┐
│ sandbox-router   │  Routes by X-Sandbox-ID header
│ :8080            │
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ Sandbox Pod      │  python-runtime :8888
│ (from template)  │  Executes command
│                  │  Returns stdout/stderr/exit_code
└──────────────────┘
```

The sandbox is **never created for conversation-only messages**. Only when the agent invokes a shell tool (`bash`, `write_file`, `read_file`, etc.) does `_ensure_sandbox()` trigger pod creation.

---

## Shell Interpretation

The agent-sandbox SDK's `commands.run()` executes commands directly (like `exec`), not through a shell. This means heredocs, pipes, redirects, and variable expansion do not work with raw command strings.

`K8sSandbox.execute()` wraps every command in `sh -c` using `shlex.quote()`:

```python
sh_command = f"sh -c {shlex.quote(command)}"
result = sandbox.commands.run(sh_command, timeout=effective_timeout)
```

This is the same pattern used by `ModalSandbox` (the reference deepagents integration, which wraps in `bash -c`). The wrapping is required because `BaseSandbox`'s `write()`, `read()`, and `edit()` methods construct commands with heredoc syntax to pass base64-encoded payloads via stdin — these only work through a shell interpreter.

---

## Scoping Labels

CognitionContext fields are mapped to `cognition.io/*` labels on the Sandbox CR, enabling multi-tenant visibility:

```python
labels = {
    "cognition.io/user": context.user_id,
    "cognition.io/org": context.org_id,
    "cognition.io/project": context.project_id,
    "cognition.io/session": session_id,
}
```

Queryable with kubectl:

```bash
kubectl get sandboxes -n cognition -l cognition.io/user=alice
```

Labels are set at SandboxClaim creation time and cannot be changed afterward. They are for observability and traceability only — not for K8s admission policy enforcement (that's a v2 hardening item).

---

## Settings

Five environment variables control the K8s sandbox backend:

| Env Var | Default | Description |
|---|---|---|
| `COGNITION_K8S_SANDBOX_TEMPLATE` | `cognition-sandbox` | SandboxTemplate CR name |
| `COGNITION_K8S_SANDBOX_NAMESPACE` | `default` | K8s namespace for sandbox CRs |
| `COGNITION_K8S_SANDBOX_ROUTER_URL` | `http://sandbox-router-svc.default.svc.cluster.local:8080` | Router service URL |
| `COGNITION_K8S_SANDBOX_TTL` | `3600` | Auto-cleanup after N seconds |
| `COGNITION_K8S_SANDBOX_WARM_POOL` | (none) | SandboxWarmPool CR name (reserved) |

Set `COGNITION_SANDBOX_BACKEND=kubernetes` to activate.

Helm values under `config.sandbox.k8s.*`:

```yaml
config:
  sandbox:
    backend: kubernetes
    k8s:
      template: cognition-sandbox
      namespace: cognition
      routerUrl: http://sandbox-router-svc.cognition.svc.cluster.local:8080
      ttl: 3600
      warmPool: ""
```

---

## Session Lifecycle

```
Session created      →  create_sandbox_backend("kubernetes", labels={...})
                         No Sandbox CR yet. Backend stores config only.
                              │
                              ▼
First tool call      →  CognitionKubernetesSandboxBackend.execute()
                         → K8sSandbox._ensure_sandbox()
                         → SandboxClient.create_sandbox()
                         → Pod appears with scoping labels + TTL
                              │
                              ▼
Subsequent calls     →  execute() routes through existing sandbox
                              │
                              ▼
Session destroyed    →  backend.terminate()  ← Wired via SessionAgentManager.unregister_session()
                         → sandbox.terminate()
                         → SandboxClaim deleted
                         → Controller deletes Sandbox + Pod
```

**TTL safety net**: If `terminate()` is never called (server crash, network partition), the controller deletes the Sandbox CR when `spec.shutdownTime` expires.

**Termination wiring**: `terminate()` is called from `SessionAgentManager.unregister_session()` when a session is deleted via `DELETE /sessions/{id}`. This ensures sandbox pods are cleaned up when sessions are destroyed.

---

## Deployment Prerequisites

When using `config.sandbox.backend: kubernetes`, the following must be installed **before** deploying Cognition:

| Prerequisite | Install | Purpose |
|---|---|---|
| agent-sandbox controller | `kubectl apply -f .../v0.3.10/manifest.yaml` | Reconciles Sandbox CRs into pods |
| agent-sandbox extensions | `kubectl apply -f .../v0.3.10/extensions.yaml` | SandboxTemplate, SandboxClaim, SandboxWarmPool CRDs |
| sandbox-router | Deploy from [agent-sandbox router](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/clients/python/agentic-sandbox-client/sandbox-router) | Proxies commands to sandbox pods |
| SandboxTemplate CR | User creates this | Defines sandbox pod spec (image, resources, security) |

These are **not** bundled in Cognition's Helm chart. The agent-sandbox controller is cluster-scoped infrastructure, not per-application.

The Cognition Helm chart creates the RBAC (Role + RoleBinding) automatically when `backend=kubernetes`.

---

## Helm Chart

### RBAC (conditional on `backend=kubernetes`)

Namespace-scoped Role for sandbox lifecycle:

```yaml
rules:
  - apiGroups: ["agents.x-k8s.io"]
    resources: ["sandboxes"]
    verbs: ["get", "list", "watch", "patch"]          # patch for shutdownTime
  - apiGroups: ["extensions.agents.x-k8s.io"]
    resources: ["sandboxclaims", "sandboxtemplates"]
    verbs: ["get", "list", "watch", "create", "delete"]  # SDK lifecycle
```

Cluster-scoped ClusterRole for startup validation (CRD existence checks):

```yaml
rules:
  - apiGroups: ["apiextensions.k8s.io"]
    resources: ["customresourcedefinitions"]
    verbs: ["get", "list"]
    resourceNames:
      - sandboxes.agents.x-k8s.io
      - sandboxclaims.extensions.agents.x-k8s.io
      - sandboxtemplates.extensions.agents.x-k8s.io
```

Both are created automatically by the Helm chart when `backend=kubernetes`.

### Example SandboxTemplate

See [`deploy/examples/cognition-sandbox-template.yaml`](../../deploy/examples/cognition-sandbox-template.yaml).

The template must include writable volume mounts for `/tmp` and `/workspace`. The runtime image uses `readOnlyRootFilesystem: true` for security, which makes the root filesystem read-only. Without writable mount points, `BaseSandbox` file operations that write temporary data (e.g., heredoc payloads) will fail with "Read-only file system" errors.

### NetworkPolicy (optional)

Set `config.sandbox.k8s.denyEgress: true` in Helm values to deny all egress from sandbox pods. This is the K8s equivalent of Docker's `network_mode: "none"`.

### Startup Validation

When `sandbox_backend=kubernetes`, the server validates at startup that:
1. The `sandboxes.agents.x-k8s.io` CRD exists (fatal if missing)
2. The `sandboxclaims.extensions.agents.x-k8s.io` CRD exists (fatal if missing)
3. The router health endpoint (`/healthz`) is reachable (warning if not)

If CRDs are missing, the server fails to start with a clear error message including install commands.

---

## Live Demo Results

Verified on a Talos Linux cluster (amd64, K8s v1.34.1):

```
Creating sandbox...
Sandbox sandbox-claim-a71288fe is ready.     # ~2s (image cached)
Running: echo Hello from K8s sandbox!
  stdout: Hello from K8s sandbox!
  exit_code: 0
Running: python3 platform check
  stdout: sandbox-claim-a71288fe 3.11.15
Running: uname -a
  stdout: Linux sandbox-claim-a71288fe 6.12.48-talos x86_64 GNU/Linux
Terminating sandbox...
Terminated SandboxClaim: sandbox-claim-a71288fe
```

First sandbox on a fresh node took ~17s (image pull). Subsequent sandboxes took ~2s.

---

## E2E Tests

K8s sandbox integration tests are in `tests/e2e/test_k8s_sandbox_e2e.py`. They are skipped unless `COGNITION_K8S_E2E=1` is set (same pattern as other e2e tests that require external infrastructure).

```bash
# Port-forward the sandbox-router and Cognition server
kubectl port-forward svc/sandbox-router-svc -n cognition 8081:8080 &
kubectl port-forward svc/cognition -n cognition 8000:8000 &

# Run tests
COGNITION_K8S_E2E=1 COGNITION_K8S_E2E_ROUTER_URL=http://localhost:8081 \
    uv run pytest tests/e2e/test_k8s_sandbox_e2e.py -v
```

Test coverage:

| Class | Tests | What it verifies |
|---|---|---|
| `TestK8sSandboxLifecycle` | 2 | API-level session create/message/delete with sandbox cleanup |
| `TestK8sSandboxDirectBackend` | 9 | Direct `K8sSandbox` operations: execute, Python, write/read, edit, upload/download, labels, TTL, terminate, lazy init |
| `TestK8sSandboxStartupValidation` | 2 | Cluster prerequisites: CRDs exist, SandboxTemplate exists |

---

## Known Gaps

| Gap | Impact | Priority |
|---|---|---|
| Warm pool not implemented | First tool call pays cold-start latency | Low |
| Native SDK file transfer | Base64-through-execute is v1 only | Low |

---

## Security Considerations

The K8s sandbox provides equivalent isolation to the Docker backend, enforced by the K8s control plane:

| Boundary | Mechanism |
|---|---|
| Process | Separate pod per session, own PID namespace |
| Network | Pod securityContext (add NetworkPolicy for egress denial) |
| Filesystem | `readOnlyRootFilesystem: true`, `emptyDir` for workspace |
| Capabilities | `capabilities.drop: ["ALL"]` |
| Resources | `resources.limits` on SandboxTemplate |
| Auto-cleanup | TTL-based deletion via agent-sandbox controller |

**Secrets**: Never placed inside the sandbox. API keys and credentials stay in the Cognition server process. If an agent needs authenticated API access, define tools that run outside the sandbox.

For production, apply a NetworkPolicy to deny sandbox egress:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: sandbox-deny-egress
spec:
  podSelector:
    matchLabels:
      agents.x-k8s.io/sandbox: "true"
  policyTypes:
    - Egress
  egress: []
```

---

## Layer Assignment

| Component | Layer |
|---|---|
| `langchain-k8s-sandbox` package | Layer 3 (Execution) |
| `CognitionKubernetesSandboxBackend` | Layer 4 (Agent Runtime) + Layer 3 |
| Settings fields | Layer 1 (Foundation) |
| Helm RBAC + values | Layer 1 (Foundation) |
| SandboxTemplate | Layer 1 (Foundation, prerequisite) |

No upward imports. `langchain-k8s-sandbox` (Layer 3) has no Cognition dependency. `CognitionKubernetesSandboxBackend` (Layer 4) imports from Layer 3 only.
