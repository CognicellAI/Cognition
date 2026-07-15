# AWS Lambda MicroVM Sandbox Example

This example shows how a builder can prepare AWS prerequisites for Cognition's
`aws_lambda_microvm` sandbox backend.

It includes:

- a reusable Terraform template for IAM and optional VPC egress prerequisites
- a sample `.cognition/config.yaml`
- a sample `POST /agents` payload

The Terraform consumes a prebuilt Lambda MicroVM image ARN. It does not build,
publish, or update MicroVM images.

## Need an Image?

If you do not already have a Lambda MicroVM image, use
[`examples/aws-lambda-microvm-default-runtime`](../aws-lambda-microvm-default-runtime/)
first. That example packages Cognition's commented default runtime source,
creates a builder-owned Lambda MicroVM image, and outputs the image ARN/version
used by this example.

## Prerequisites

You need:

- Terraform `>= 1.5`
- AWS provider credentials with IAM, Lambda, EC2, and Cloud Control access
- an AWS region where Lambda MicroVMs are available
- a prebuilt Lambda MicroVM image ARN
- a runtime command server in that image exposing:
  - `GET /healthz`
  - `POST /execute`
  - `POST /upload`
  - `GET` or `POST /download`

The Cognition runtime must be installed with:

```bash
uv sync --extra aws-lambda-microvms
```

## Terraform Setup

Copy the example variables:

```bash
cd examples/aws-lambda-microvm-sandbox/terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
aws_region                 = "us-west-2"
prebuilt_microvm_image_arn = "arn:aws:lambda:us-west-2:123456789012:microvm-image:cognition-runtime"
```

Initialize and validate:

```bash
terraform init
terraform validate
terraform plan
```

Apply only after reviewing the plan:

```bash
terraform apply
```

After apply, print Cognition profile YAML:

```bash
terraform output -raw sandbox_profiles_yaml
```

Copy that output into `.cognition/config.yaml` or register the profile with
`POST /sandbox/profiles`.

## What Terraform Creates

Default resources:

- IAM role for default agent execution.
- IAM policy for the Cognition control plane.
- Optional policy attachments to existing Cognition control-plane roles.
- AWS-managed internet connector ARNs in generated profile YAML.
- Cost-oriented sandbox profile defaults: max duration, idle/suspend policy, and
  runtime logging disabled unless CloudWatch logging is explicitly selected.

Optional resources when `create_vpc_egress_connector = true`:

- Security group for VPC egress.
- Lambda Network Connector through `awscc_lambda_network_connector`.
- VPC sandbox profile YAML.

## Cognition Config

The sample `.cognition/config.yaml` uses placeholder ARNs. Replace them with
Terraform outputs before running Cognition:

```bash
COGNITION_SANDBOX_BACKEND=aws_lambda_microvm \
COGNITION_AWS_LAMBDA_MICROVM_DEFAULT_PROFILE=default-lambda \
AWS_REGION=us-west-2 \
uv run --extra aws-lambda-microvms uvicorn server.app.main:app --reload --port 8000
```

## Smoke Test

Confirm the backend is advertised:

```bash
curl http://localhost:8000/capabilities | jq '.sandbox_backends'
```

Create the sample agent:

```bash
curl -X POST http://localhost:8000/agents \
  -H 'Content-Type: application/json' \
  --data @examples/aws-lambda-microvm-sandbox/api/agent-create.json
```

Create a session and ask the agent to run a simple command:

```bash
SESSION_ID=$(
  curl -s -X POST http://localhost:8000/sessions \
    -H 'Content-Type: application/json' \
    -d '{"agent_name": "lambda-microvm-smoke", "title": "MicroVM smoke test"}' \
  | jq -r '.id'
)

curl -N -X POST "http://localhost:8000/sessions/${SESSION_ID}/messages" \
  -H 'Content-Type: application/json' \
  -d '{"content": "Run pwd and write the result to /workspace/pwd.txt."}'
```

Look for `sandbox_lifecycle` events with `sandbox_backend:
"aws_lambda_microvm"` and token-free runtime metadata. The metadata includes
safe correlation fields for the Cognition session, run, agent, profile, scope
keys, and scope fingerprint.

## Cost Controls

The generated `sandbox_profiles_yaml` includes the Lambda MicroVM settings most
likely to affect runtime cost:

- `maximum_duration_seconds` caps how long a MicroVM can run.
- `idle_policy` lets AWS suspend idle MicroVMs while preserving resume state.
- `logging` defaults to `disabled: {}` so noisy runtime output is not sent to
  CloudWatch unless you opt in.
- `quota` caps Cognition-side concurrent sessions and session starts per minute
  for each profile/scope pair.
- `egress_mode` and connector ARNs make internet versus VPC egress explicit.

To enable CloudWatch runtime logs, set:

```hcl
microvm_logging_mode = "cloudwatch"
microvm_log_group    = "/aws/lambda-microvms/cognition"
microvm_log_stream   = "default-lambda"
```

## Cleanup

Delete Cognition sessions first so running MicroVMs can terminate:

```bash
curl -X DELETE "http://localhost:8000/sessions/${SESSION_ID}"
```

Then destroy Terraform-owned prerequisites:

```bash
cd examples/aws-lambda-microvm-sandbox/terraform
terraform destroy
```

Terraform does not delete or modify your prebuilt MicroVM image.

## Security Notes

- Restrict the control-plane policy to approved MicroVM images, network
  connectors, and execution roles.
- Add only approved per-agent execution role ARNs to
  `additional_agent_execution_role_arns`.
- Do not commit `terraform.tfvars`, Terraform state, AWS credentials, or proxy
  auth tokens.
- Cognition persists MicroVM id, endpoint, profile, image, status, and role
  fingerprint; it never persists proxy auth tokens.

## Related Docs

- [AWS Lambda MicroVM Sandbox concept](../../docs/concepts/sandboxes/aws-lambda-microvm/index.md)
- [Default runtime image](../../docs/concepts/sandboxes/aws-lambda-microvm/default-runtime-image.md)
- [AWS Lambda MicroVM Sandbox setup guide](../../docs/concepts/sandboxes/aws-lambda-microvm/setup.md)
- [API Reference: Sandbox Profiles](../../docs/guides/api-reference.md#sandbox-profiles)
