# AWS Lambda MicroVM Sandbox

The `aws_lambda_microvm` sandbox backend runs agent command work in AWS Lambda
MicroVMs. Use it when you want an AWS-native execution boundary, per-agent IAM
execution roles, and profile-controlled networking without running Docker or
Kubernetes sandbox infrastructure yourself.

Cognition consumes prebuilt Lambda MicroVM image ARNs. It does not build,
publish, or mutate MicroVM images at runtime. Builders who need a starting image
can build one from Cognition's [default runtime image source](./default-runtime-image.md).

## Architecture

```mermaid
flowchart TD
    Agent[AgentDefinition] -->|sandbox_profile| Profile[SandboxProfile]
    Agent -->|sandbox_execution_role_arn| Role[Trusted execution role]
    Profile --> Backend[CognitionAwsLambdaMicroVmSandboxBackend]
    Role --> Backend
    Backend --> Package[packages/langchain-aws-lambda-microvms]
    Package -->|RunMicroVM| MicroVM[AWS Lambda MicroVM]
    Package -->|CreateMicroVMAuthToken| Token[Proxy token in memory]
    Package -->|HTTPS proxy request| Runtime[Runtime command server]
    Runtime --> Tools[/execute /upload /download]
```

## Ownership Model

| Owner | Responsibilities |
|---|---|
| Builder | AWS account, control-plane IAM identity, execution roles, image creation, optional VPC/network connectors |
| Cognition | Profile and agent resolution, trusted role selection, lifecycle events, quota checks, token-free metadata |
| AWS | MicroVM launch, proxy-authenticated control plane, network connector enforcement, runtime logs when enabled |
| Runtime image | `/healthz`, `/execute`, `/upload`, `/download`, and optional lifecycle hooks |

The model never chooses image ARNs, IAM roles, connectors, or auth tokens through
LLM output or tool-call arguments. Those values come from trusted Cognition
configuration.

## Session-Scoped Lifecycle

The MicroVM sandbox is scoped to the Cognition session, not to one message run.
A successful run returns the session to `idle` and keeps the sandbox available
for follow-up work on the same conversation. Cleanup happens when the session is
deleted, aborted, failed, expired, or released by backend policy.

## Start Here

- [Setup](./setup.md)
- [Default Runtime Image](./default-runtime-image.md)
- [Sandbox Profiles](./profiles.md)
- [IAM and Networking](./iam-and-networking.md)
- [Runtime Command Server](./runtime-command-server.md)
- [Lifecycle and Observability](./lifecycle-and-observability.md)
- [Troubleshooting](./troubleshooting.md)
