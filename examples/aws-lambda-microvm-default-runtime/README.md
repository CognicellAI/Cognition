# AWS Lambda MicroVM Default Runtime

This example builds a builder-owned AWS Lambda MicroVM image from Cognition's
default runtime source. Use it when you need a starting image for the
`aws_lambda_microvm` sandbox backend.

The output is a Lambda MicroVM image ARN in your AWS account. Cognition then
uses that ARN from a `SandboxProfile`.

## What This Example Provides

- commented runtime image source under `runtime/`
- a packaging script that creates the AWS source zip
- Terraform that uploads the zip to S3 and creates the MicroVM image
- `sandbox_profiles_yaml` output for direct Cognition configuration

This is the Lambda MicroVM equivalent of a default `cognition-sandbox` image,
but the final AWS image is owned by the builder account.

## Prerequisites

You need:

- Terraform `>= 1.5`
- AWS provider credentials with IAM, S3, CloudWatch Logs, and Lambda MicroVM
  image permissions
- an AWS region where Lambda MicroVMs are available
- `zip` available locally

The AWS identity running Terraform must be able to create the image build role,
upload the source artifact, and call the Lambda MicroVM image APIs.

## Build The Runtime Source Zip

```bash
cd examples/aws-lambda-microvm-default-runtime
./scripts/package-runtime.sh
```

This creates:

```text
dist/cognition-lambda-microvm-runtime.zip
```

The zip contains only the files Lambda needs to build the image:

- `Dockerfile`
- `server.py`
- `README.md`

## Create The MicroVM Image

Copy the example variables:

```bash
cd examples/aws-lambda-microvm-default-runtime/terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` for your account and region, then run:

```bash
terraform init
terraform validate
terraform plan
terraform apply
```

After apply, get the image outputs:

```bash
terraform output microvm_image_arn
terraform output microvm_image_version
terraform output -raw sandbox_profiles_yaml
```

Copy `sandbox_profiles_yaml` into `.cognition/config.yaml` or register it with
`POST /sandbox/profiles`.

## Use With Cognition

The generated profile uses the `aws_lambda_microvm` backend and points to the
builder-owned image ARN.

```yaml
sandbox_profiles:
  default-lambda:
    backend: aws_lambda_microvm
    image_arn: arn:aws:lambda:us-west-2:123456789012:microvm-image:cognition-default-runtime
    image_version: "1.0"
    region: us-west-2
    port: 8080
```

You still need the normal sandbox prerequisites from
[`examples/aws-lambda-microvm-sandbox`](../aws-lambda-microvm-sandbox/):

- Cognition control-plane permissions
- an agent execution role
- optional VPC egress connector

## Customize Safely

Fork `runtime/` when your agents need more tools. Common additions include:

- language runtimes such as Node.js, Go, or Java
- package managers and CLIs
- organization CA certificates
- observability agents
- stricter command policy inside `server.py`

After changing runtime files, rerun `./scripts/package-runtime.sh`, then apply
Terraform again to create a new MicroVM image version.

## Cleanup

Destroy the image and artifact resources with:

```bash
cd examples/aws-lambda-microvm-default-runtime/terraform
terraform destroy
```

Generated zips, Terraform state, and local variable files are ignored by git.

## Related Docs

- [Default runtime image](../../docs/concepts/sandboxes/aws-lambda-microvm/default-runtime-image.md)
- [Runtime command server](../../docs/concepts/sandboxes/aws-lambda-microvm/runtime-command-server.md)
- [Lambda MicroVM setup](../../docs/concepts/sandboxes/aws-lambda-microvm/setup.md)
