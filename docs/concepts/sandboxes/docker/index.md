# Docker Sandbox

The `docker` sandbox backend runs agent commands in a container created for the
session. It is the default production-style backend for single-node deployments
where the Cognition server can access Docker.

## Behavior

- A container is created lazily when the agent first needs command execution.
- The container is reused for follow-up work in the same session while it is
  alive.
- The backend can run with container resource limits and network isolation.
- Cognition still protects `.cognition/` from agent write/edit/delete
  operations before commands reach the backend.

## When To Use It

Use `docker` when:

- Cognition runs on a VM or host with Docker available
- you want stronger process isolation than `local`
- you do not need Kubernetes-native or AWS-native sandbox lifecycle

Use `kubernetes` instead when Cognition runs inside a locked-down K8s pod
without Docker socket access. Use `aws_lambda_microvm` when you want AWS-managed
MicroVM isolation and per-agent IAM execution roles.

## Configuration

```yaml
sandbox:
  backend: docker
```

Or set:

```bash
COGNITION_SANDBOX_BACKEND=docker
```

## Related

- [Sandboxes](../index.md)
- [Deployment](../../../guides/deployment.md)
- [Security](../../security.md#sandbox-isolation)
