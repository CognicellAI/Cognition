# langchain-k8s-sandbox

[deepagents](https://github.com/langchain-ai/deepagents) sandbox backend for Kubernetes using the [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) CRD and controller.

## Installation

```bash
pip install langchain-k8s-sandbox[k8s]
```

The `[k8s]` extra installs `k8s-agent-sandbox` (the agent-sandbox Python SDK) and `kubernetes` (for TTL patching).

## Usage

```python
from langchain_k8s_sandbox import K8sSandbox

sandbox = K8sSandbox(
    template="cognition-sandbox",
    namespace="default",
    router_url="http://sandbox-router-svc.default.svc.cluster.local:8080",
    labels={"cognition.io/user": "alice"},
    ttl=3600,
)

result = sandbox.execute("echo hello")
print(result.output)      # "hello\n"
print(result.exit_code)   # 0

sandbox.terminate()
```

## File Operations

All standard `BaseSandbox` file operations are inherited and work through `execute()`:

- `write(path, content)` — create file
- `read(path)` — read file
- `edit(path, old, new)` — find-and-replace in file
- `ls_info(path)` — list directory
- `glob_info(path, pattern)` — find files by pattern
- `grep_raw(path, pattern, include)` — search file contents

Commands are wrapped in `sh -c` so that shell features (heredocs, pipes, redirects) work correctly. The agent-sandbox SDK's `commands.run()` executes commands directly without a shell, so this wrapping is required for `BaseSandbox`'s heredoc-based file operations.

## TTL (Auto-Cleanup)

Set `ttl` to automatically delete the sandbox after N seconds. This is implemented by patching the Sandbox CR's `spec.shutdownTime` field via the Kubernetes API. Two cleanup paths exist: explicit `terminate()` and the TTL safety net.

## Requirements

- [agent-sandbox controller](https://github.com/kubernetes-sigs/agent-sandbox) v0.3.10+ installed in your Kubernetes cluster
- [agent-sandbox extensions](https://github.com/kubernetes-sigs/agent-sandbox) (SandboxTemplate, SandboxClaim CRDs)
- sandbox-router service running and accessible
- SandboxTemplate CR matching the `template` parameter with writable `/tmp` and `/workspace` volume mounts

## Design

See [DESIGN.md](./DESIGN.md) for architecture, RBAC requirements, lifecycle details, and v1/v2 scope.
