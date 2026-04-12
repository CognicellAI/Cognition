# langchain-k8s-sandbox Design

A [deepagents](https://github.com/langchain-ai/deepagents) sandbox backend that runs commands in Kubernetes Sandbox CRs managed by [kubernetes-sigs/agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox).

Follows the `langchain-<provider>` integration convention: standalone package, zero coupling to any specific agent framework or application. Any deepagents user can `pip install langchain-k8s-sandbox[k8s]` and get K8s sandboxed execution.

---

## Prerequisites

The following must exist in the cluster before using this package:

| Dependency | Purpose |
|---|---|
| [agent-sandbox controller](https://github.com/kubernetes-sigs/agent-sandbox) (v0.3.10+) | Reconciles Sandbox CRs into pods |
| [agent-sandbox extensions](https://github.com/kubernetes-sigs/agent-sandbox) | SandboxTemplate, SandboxClaim, SandboxWarmPool CRDs |
| sandbox-router Deployment + Service | Proxies HTTP commands from callers to sandbox pods via `X-Sandbox-ID` header |
| SandboxTemplate CR | Defines the pod spec (image, resources, security context) for sandbox containers |
| Runtime image | Container image that listens on port 8888 for command execution (e.g. `python-runtime-sandbox`) |

Install controller + extensions:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.3.10/manifest.yaml
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.3.10/extensions.yaml
```

---

## Architecture

```
K8sSandbox (this package)
    │
    │  SandboxClient.create_sandbox()
    ▼
K8s API Server ──▶ SandboxClaim CR ──▶ controller reconciles ──▶ Sandbox CR ──▶ Pod
    │                                                                     │
    │  sandbox.commands.run()                                             │
    ▼                                                                     ▼
sandbox-router-svc:8080 ──(X-Sandbox-ID header)──▶ sandbox pod :8888
                                                      │
                                                      ▼
                                                 sh -c <command>
                                                      │
                                                      ▼
                                                 Executes command
                                                 Returns stdout/stderr/exit_code
```

**Two paths**:
- **Lifecycle** (create/terminate): Goes through the K8s API directly. The SDK creates/deletes `SandboxClaim` CRs, which the controller reconciles into `Sandbox` CRs and pods.
- **Execution** (run commands): Goes through the sandbox-router HTTP proxy. The router looks up the target pod by sandbox ID and forwards the request.

---

## Shell Interpretation (`sh -c` wrapping)

The agent-sandbox SDK's `commands.run()` executes commands directly (like `exec`), not through a shell. This means shell features — heredocs, pipes, redirects, variable expansion — do not work when passed as a raw command string.

Since `BaseSandbox`'s `write()`, `read()`, and `edit()` methods construct commands that use heredoc syntax (`<<'__DEEPAGENTS_EOF__'`) to pass base64-encoded payloads via stdin, a shell interpreter is required for these inherited file operations to work.

`K8sSandbox.execute()` wraps every command in `sh -c` via `shlex.quote()`:

```python
sh_command = f"sh -c {shlex.quote(command)}"
result = sandbox.commands.run(sh_command, timeout=effective_timeout)
```

This follows the same pattern as `ModalSandbox` (the reference deepagents sandbox integration), which wraps in `bash -c`. We use `sh` because it is POSIX-compliant and supports heredocs, which is sufficient for all `BaseSandbox` operations.

---

## API Surface

```python
class K8sSandbox(BaseSandbox):
    def __init__(
        self,
        template: str = "cognition-sandbox",     # SandboxTemplate CR name
        namespace: str = "default",              # K8s namespace for CRs
        router_url: str = "http://sandbox-router-svc.default.svc.cluster.local:8080",
        labels: dict[str, str] | None = None,    # Applied to the SandboxClaim CR
        ttl: int | None = None,                  # Auto-cleanup after N seconds
        server_port: int = 8888,                 # Sandbox runtime listen port
        warm_pool: str | None = None,            # SandboxWarmPool CR name (reserved)
    ): ...

    @property
    def id(self) -> str: ...                     # "k8s-<hex>" or resolved sandbox name

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse: ...
    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]: ...
    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]: ...
    def terminate(self) -> None: ...
```

All other file operations (`read`, `write`, `edit`, `ls`, `grep`, `glob`) are inherited from `BaseSandbox`, which constructs shell commands and pipes them through `execute()`. These work correctly because `execute()` wraps commands in `sh -c`, enabling heredoc and pipe support.

---

## SandboxTemplate Requirements

The SandboxTemplate CR must include writable volume mounts for `/tmp` and `/workspace`. The default runtime image uses `readOnlyRootFilesystem: true` for security, which makes the root filesystem read-only. Without writable mount points, `BaseSandbox` file operations that write temporary data (e.g., heredoc payloads) will fail.

```yaml
spec:
  podTemplate:
    spec:
      containers:
      - name: python-runtime
        securityContext:
          readOnlyRootFilesystem: true
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

`/tmp` is required by `BaseSandbox`'s heredoc-based file operations. `/workspace` is the default agent working directory. Both are `emptyDir` volumes — data is lost when the Sandbox CR is deleted.

---

## Lazy Initialization Lifecycle

Sandbox CRs are not created until the first `execute()` call. This avoids paying for pods in sessions that only involve conversation.

```
__init__()          Stores config. No SDK calls, no K8s API calls.
    │
    ▼
first execute()     _ensure_sandbox() called internally:
                    1. SandboxClient(SandboxDirectConnectionConfig)
                    2. client.create_sandbox(template, namespace, labels)
                    3. SDK watches Sandbox CR until Ready
                    4. If ttl set: _apply_shutdown_time() patches spec.shutdownTime
                    │
                    ▼
execute()           sandbox.commands.run("sh -c <quoted-command>", timeout) → ExecuteResponse
    │
    ▼
terminate()         sandbox.terminate() → deletes SandboxClaim → controller deletes Sandbox + Pod
                    Resets internal state. Next execute() creates a new sandbox.
```

Spin-up timing observed on a Talos Linux cluster:
- First sandbox on a node: ~15-20s (includes image pull)
- Subsequent sandboxes on same node: ~2s (image cached)

---

## TTL Mechanism

The SDK's `create_sandbox()` signature does not expose a TTL parameter:

```python
def create_sandbox(self, template, namespace, sandbox_ready_timeout, labels)
```

To enforce auto-cleanup, `_apply_shutdown_time()` patches the `Sandbox` CR directly after creation:

```python
from kubernetes import client as k8s_client
from kubernetes.config import load_incluster_config

load_incluster_config()
api = k8s_client.CustomObjectsApi()
body = {"spec": {"shutdownTime": "2026-04-09T20:16:00Z"}}
api.patch_namespaced_custom_object(
    group="agents.x-k8s.io", version="v1alpha1",
    namespace=namespace, plural="sandboxes",
    name=sandbox_name, body=body,
)
```

This uses `load_incluster_config()` first (production), then falls back to `load_kube_config()` (reads `~/.kube/config` for local dev/CI). If neither config is available, the error is logged as a warning and execution continues — the sandbox still works, it just won't auto-clean up.

**Two cleanup paths** (both are active):
1. `terminate()` — explicit deletion when the caller shuts down
2. `shutdownTime` — safety net for crashed/abandoned sessions where `terminate()` is never called

---

## File Transfer

v1 uses `BaseSandbox`'s default approach: base64-encode file content and pipe through `execute()`. This works for all file types but is slow for large files.

```python
# Upload: base64 encode → pipe through sh -c
encoded = base64.b64encode(content).decode("ascii")
execute(f"mkdir -p $(dirname {file_path}) && echo '{encoded}' | base64 -d > {file_path}")

# Download: base64 encode → read via sh -c
result = execute(f"base64 {file_path}")
content = base64.b64decode(result.output.strip())
```

v2 will use the SDK's native `sandbox.files.upload()` / `sandbox.files.download()` when available.

---

## RBAC Requirements

The calling pod's ServiceAccount needs both namespace-scoped and cluster-scoped permissions.

### Namespace-scoped Role (sandbox lifecycle)

```yaml
rules:
  - apiGroups: ["agents.x-k8s.io"]
    resources: ["sandboxes"]
    verbs: ["get", "list", "watch", "patch"]       # patch for shutdownTime
  - apiGroups: ["agents.x-k8s.io"]
    resources: ["sandboxes/status"]
    verbs: ["get"]
  - apiGroups: ["extensions.agents.x-k8s.io"]
    resources: ["sandboxclaims"]
    verbs: ["get", "list", "watch", "create", "delete"]  # SDK creates/deletes claims
  - apiGroups: ["extensions.agents.x-k8s.io"]
    resources: ["sandboxtemplates"]
    verbs: ["get", "list", "watch"]                # SDK reads template on create
```

### Cluster-scoped ClusterRole (startup validation)

The startup validation check reads CRDs to verify the agent-sandbox controller is installed. CRDs are cluster-scoped resources, so a ClusterRole is required:

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

The `create`/`delete` on `sandboxclaims` is required because the SDK manages the claim lifecycle directly — `create_sandbox()` creates a claim, `terminate()` deletes it.

---

## Startup Validation

When the caller configures `sandbox_backend=kubernetes`, the server validates at startup that:

1. The agent-sandbox CRDs exist (fatal if missing — clear error with install commands)
2. The sandbox-router health endpoint (`/healthz`) is reachable (warning if not — sandbox creation will fail at runtime instead)

This is implemented in `validate_k8s_sandbox_config()` and called from the application lifespan. It prevents silent failures where the server starts successfully but every sandbox `execute()` call fails due to missing infrastructure.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| SDK not installed (`k8s-agent-sandbox` missing) | `RuntimeError` on first `execute()` with install instructions |
| Sandbox creation fails (CRD missing, RBAC denied) | `RuntimeError` from `_ensure_sandbox()` |
| Command execution fails (timeout, connection refused) | Returns `ExecuteResponse(output="Error: ...", exit_code=-1)` |
| `terminate()` fails (already deleted, RBAC) | Logs warning, does not raise. Safe to call multiple times. |
| `_apply_shutdown_time()` fails | Logs warning. Sandbox still works, just no auto-cleanup. |
| `execute()` called after `terminate()` | Creates a new sandbox (lazy re-init) |

---

## v1 Scope

**Included**: Sync `execute()` with `sh -c` wrapping, lazy init, labels passthrough, TTL via CR patch, base64 file transfer, `terminate()`, DirectConnection mode, startup validation.

**Deferred to v2**:

| Feature | Reason |
|---|---|
| `aexecute()` / async client | deepagents wraps sync `execute()` in `asyncio.to_thread()` automatically |
| Native SDK file upload/download | SDK file API not yet stable across providers |
| Gateway / Tunnel connection modes | DirectConnection covers in-cluster production; dev can `kubectl port-forward` |
| Warm pool allocation | Settings field is reserved; SDK supports it but integration is deferred |
