# Lambda MicroVM Sandbox Profiles

A `SandboxProfile` is builder-managed configuration for a class of Lambda
MicroVM sandbox. Agents select profiles by name with `sandbox_profile`.

Profiles can be seeded from `.cognition/config.yaml` or managed at runtime with
`/sandbox/profiles`.

## Profile Fields

| Field | Purpose |
|---|---|
| `name` | Stable selector used by agents |
| `backend` | Must be `aws_lambda_microvm` |
| `image_arn` | Prebuilt Lambda MicroVM image ARN |
| `image_version` | Optional image version |
| `region` | AWS region; defaults to the ARN region when omitted |
| `ingress_network_connector_arns` | Optional ingress connector ARNs |
| `egress_mode` | `internet` or `vpc` |
| `egress_network_connector_arns` | Required when `egress_mode` is `vpc` |
| `idle_policy` | Optional Lambda MicroVM idle and suspend policy |
| `logging` | `disabled: {}` or CloudWatch logging configuration |
| `quota` | Cognition-side profile/scope quota policy |
| `run_hook_payload` | Optional payload for the image `/run` lifecycle hook |
| `maximum_duration_seconds` | Maximum MicroVM lifetime, up to 28800 seconds |
| `port` | Runtime command server port inside the MicroVM |
| `token_expiration_minutes` | AWS proxy auth token lifetime requested by Cognition |
| `default_execution_role_arn` | IAM role used when an agent does not specify one |
| `scope` | Builder-defined ConfigStore scope restriction |

## Image Contract

V1 profiles consume prebuilt Lambda MicroVM image ARNs:

```yaml
image_arn: arn:aws:lambda:us-west-2:123456789012:microvm-image:cognition-runtime
```

Cognition does not create or update images. Builders own image contents,
publishing, patching, and runtime command server compatibility.

## Cost-Sensitive Settings

| Setting | Cost impact |
|---|---|
| `maximum_duration_seconds` | Hard upper bound on MicroVM lifetime |
| `idle_policy` | Lets AWS suspend or resume idle MicroVMs when supported |
| `quota.max_concurrent_sessions` | Caps live sandbox sessions per profile/scope |
| `quota.max_session_starts_per_minute` | Caps burst launch rate per profile/scope |
| `logging` | CloudWatch logging can add ingestion and retention cost |
| `egress_mode` and connectors | VPC and NAT paths can add network cost |

Use `logging.disabled: {}` by default unless you need runtime logs for
investigation.

## Quota Scope

Cognition enforces quotas per sandbox profile and effective-scope fingerprint.
That means two tenants can use the same profile without consuming each other's
concurrent-session budget.

Completed runs return the session to `idle` and keep the sandbox available for
follow-up work. Delete, abort, fail, expire, or otherwise clean up sessions you
no longer need so concurrent-session quota is released.

## Related

- [Setup](./setup.md)
- [Lifecycle and Observability](./lifecycle-and-observability.md)
- [API Reference](../../../guides/api-reference.md#sandbox-profiles)
