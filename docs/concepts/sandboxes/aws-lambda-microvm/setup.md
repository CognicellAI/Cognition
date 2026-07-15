# AWS Lambda MicroVM Setup

This guide enables the `aws_lambda_microvm` sandbox backend for Cognition.

## Prerequisites

You need:

- an AWS account in a region where Lambda MicroVMs are available
- AWS credentials for the Cognition control plane
- a prebuilt Lambda MicroVM image ARN
- a runtime command server in that image
- at least one IAM execution role for sandboxed agent work
- optional Lambda Network Connector ARNs for VPC egress

If you do not already have a Lambda MicroVM image, start with the
[default runtime image](./default-runtime-image.md) example. It creates a
builder-owned image ARN from Cognition's commented runtime source files.

The Terraform example in
[`examples/aws-lambda-microvm-sandbox`](https://github.com/CognicellAI/Cognition/tree/main/examples/aws-lambda-microvm-sandbox)
creates IAM roles, an optional VPC egress connector, and Cognition-ready
`sandbox_profiles` YAML. It does not build MicroVM images.

## Install

```bash
uv sync --extra aws-lambda-microvms
```

For local development:

```bash
COGNITION_SANDBOX_BACKEND=aws_lambda_microvm \
COGNITION_AWS_LAMBDA_MICROVM_DEFAULT_PROFILE=default-lambda \
AWS_REGION=us-west-2 \
uv run --extra aws-lambda-microvms uvicorn server.app.main:app --reload --port 8000
```

## Configure a Profile

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
    logging:
      disabled: {}
    quota:
      max_concurrent_sessions: 10
      max_session_starts_per_minute: 30
    default_execution_role_arn: arn:aws:iam::123456789012:role/cognition-agent-runtime
```

Profiles can also be managed through `/sandbox/profiles`.

## Configure an Agent

```yaml
agents:
  - name: repo-maintainer
    system_prompt: "You maintain Python repositories."
    sandbox_profile: default-lambda
    sandbox_execution_role_arn: arn:aws:iam::123456789012:role/repo-maintainer-runtime
```

If `sandbox_execution_role_arn` is omitted, Cognition uses the profile's
`default_execution_role_arn`.

## Smoke Test

```bash
curl http://localhost:8000/capabilities | jq '.sandbox_backends, .features'
curl http://localhost:8000/sandbox/profiles/default-lambda | jq
```

Create a session:

```bash
SESSION_ID=$(
  curl -s -X POST http://localhost:8000/sessions \
    -H 'Content-Type: application/json' \
    -d '{"agent_name": "repo-maintainer", "title": "Lambda MicroVM smoke test"}' \
  | jq -r '.id'
)
```

Send a command-oriented prompt:

```bash
curl -N -X POST "http://localhost:8000/sessions/${SESSION_ID}/messages" \
  -H 'Content-Type: application/json' \
  -d '{"content": "Run pwd and then write the result to /workspace/pwd.txt."}'
```

Watch for `sandbox_lifecycle` events. Runtime snapshots should include MicroVM
id, status, image, endpoint, profile, cost-relevant profile settings, role
fingerprint, and session/run/agent/scope correlation metadata. They must not
include auth tokens.

## Related

- [Sandbox Profiles](./profiles.md)
- [Default Runtime Image](./default-runtime-image.md)
- [IAM and Networking](./iam-and-networking.md)
- [Troubleshooting](./troubleshooting.md)
