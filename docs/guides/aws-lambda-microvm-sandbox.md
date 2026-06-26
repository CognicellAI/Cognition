# AWS Lambda MicroVM Sandbox Setup

This guide shows how to enable the `aws_lambda_microvm` sandbox backend for
Cognition.

Use this backend when you want agent command execution in AWS-managed
MicroVMs, with per-agent IAM execution roles and profile-controlled network
egress.

## Prerequisites

You need:

- An AWS account in a region where Lambda MicroVMs are available.
- AWS credentials for the Cognition control plane.
- A prebuilt Lambda MicroVM image ARN.
- A runtime command server in that image that implements `/healthz`,
  `/execute`, `/upload`, and `/download`.
- An IAM execution role for the agent runtime.
- Optional Lambda Network Connector ARNs for VPC egress.

The reusable Terraform example in
[`examples/aws-lambda-microvm-sandbox`](https://github.com/CognicellAI/Cognition/tree/main/examples/aws-lambda-microvm-sandbox)
creates IAM roles, an optional VPC egress connector, and Cognition-ready
`sandbox_profiles` YAML. It does not build MicroVM images.

## Install the Extra

Install Cognition with the AWS Lambda MicroVM sandbox extra:

```bash
uv sync --extra aws-lambda-microvms
```

For local development, run the server with the same extra available in the
environment:

```bash
COGNITION_SANDBOX_BACKEND=aws_lambda_microvm \
COGNITION_AWS_LAMBDA_MICROVM_DEFAULT_PROFILE=default-lambda \
AWS_REGION=us-west-2 \
uv run --extra aws-lambda-microvms uvicorn server.app.main:app --reload --port 8000
```

## Configure a Sandbox Profile

Profiles can be seeded from `.cognition/config.yaml`:

```yaml
sandbox:
  backend: aws_lambda_microvm

sandbox_profiles:
  default-lambda:
    backend: aws_lambda_microvm
    image_arn: arn:aws:lambda:us-west-2:123456789012:microvm-image:cognition-runtime
    image_version: "1.0"
    region: us-west-2
    egress_mode: internet
    maximum_duration_seconds: 3600
    port: 8080
    token_expiration_minutes: 30
    idle_policy:
      max_idle_duration_seconds: 900
      suspended_duration_seconds: 300
      auto_resume_enabled: true
    default_execution_role_arn: arn:aws:iam::123456789012:role/cognition-agent-runtime
```

Or create a profile through the API:

```bash
curl -X POST http://localhost:8000/sandbox/profiles \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "default-lambda",
    "image_arn": "arn:aws:lambda:us-west-2:123456789012:microvm-image:cognition-runtime",
    "image_version": "1.0",
    "region": "us-west-2",
    "egress_mode": "internet",
    "maximum_duration_seconds": 3600,
    "port": 8080,
    "token_expiration_minutes": 30,
    "default_execution_role_arn": "arn:aws:iam::123456789012:role/cognition-agent-runtime"
  }'
```

For VPC egress, provide explicit connector ARNs:

```json
{
  "name": "private-lambda",
  "image_arn": "arn:aws:lambda:us-west-2:123456789012:microvm-image:cognition-runtime",
  "region": "us-west-2",
  "egress_mode": "vpc",
  "egress_network_connector_arns": [
    "arn:aws:lambda:us-west-2:123456789012:network-connector:private-egress"
  ]
}
```

`egress_mode: vpc` is rejected unless `egress_network_connector_arns` is set.

## Configure an Agent

Agents select profiles and roles from trusted config:

```yaml
agents:
  - name: repo-maintainer
    system_prompt: "You maintain Python repositories."
    sandbox_profile: default-lambda
    sandbox_execution_role_arn: arn:aws:iam::123456789012:role/repo-maintainer-runtime
```

You can also create the agent through the API:

```bash
curl -X POST http://localhost:8000/agents \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "repo-maintainer",
    "description": "Maintains Python repositories in a Lambda MicroVM sandbox.",
    "system_prompt": "You maintain Python repositories.",
    "sandbox_profile": "default-lambda",
    "sandbox_execution_role_arn": "arn:aws:iam::123456789012:role/repo-maintainer-runtime"
  }'
```

If `sandbox_execution_role_arn` is omitted, Cognition uses the profile's
`default_execution_role_arn`.

## Smoke Test

Confirm the backend and feature flags:

```bash
curl http://localhost:8000/capabilities | jq '.sandbox_backends, .features'
```

Confirm the profile is visible:

```bash
curl http://localhost:8000/sandbox/profiles/default-lambda | jq
```

Create a session and send a command-oriented prompt:

```bash
SESSION_ID=$(
  curl -s -X POST http://localhost:8000/sessions \
    -H 'Content-Type: application/json' \
    -d '{"agent_name": "repo-maintainer", "title": "Lambda MicroVM smoke test"}' \
  | jq -r '.id'
)

curl -N -X POST "http://localhost:8000/sessions/${SESSION_ID}/messages" \
  -H 'Content-Type: application/json' \
  -d '{"content": "Run pwd and then write the result to /workspace/pwd.txt."}'
```

Watch for `sandbox_lifecycle` events. The runtime snapshot should include a
MicroVM id, status, image, endpoint, profile, and role fingerprint. It must not
include auth tokens.

## Troubleshooting

| Symptom | Check |
|---|---|
| `SandboxProfile '<name>' was not resolved` | The profile name is not present in `.cognition/config.yaml` or `/sandbox/profiles` for the current scope |
| `image_arn must be an AWS Lambda MicroVM image ARN` | Use an ARN shaped like `arn:aws:lambda:<region>:<account>:microvm-image:<name>` |
| VPC profile returns validation error | Set `egress_network_connector_arns` when `egress_mode` is `vpc` |
| `AccessDenied` on `RunMicroVM` | Attach the control-plane policy to the AWS identity running Cognition |
| `AccessDenied` on `iam:PassRole` | Add the agent execution role ARN to the allowed role list in the control-plane policy |
| Auth token creation fails | Allow `lambda:CreateMicroVMAuthToken` for approved MicroVM and image resources |
| `/healthz` fails | Confirm the image runs the runtime command server on the profile `port` |
| Commands hang or cannot reach dependencies | Check the profile egress mode and connector ARNs |

## Related

- [AWS Lambda MicroVM Sandbox concept](../concepts/aws-lambda-microvm-sandbox.md)
- [Configuration Reference](./configuration.md#sandbox-execution)
- [API Reference](./api-reference.md#sandbox-profiles)
