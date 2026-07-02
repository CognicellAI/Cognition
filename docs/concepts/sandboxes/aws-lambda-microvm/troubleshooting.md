# Lambda MicroVM Troubleshooting

Use this page when a Lambda MicroVM sandbox fails to launch, connect, execute,
or clean up as expected.

| Symptom | Check |
|---|---|
| `SandboxProfile '<name>' was not resolved` | The profile name exists in `.cognition/config.yaml` or `/sandbox/profiles` for the current scope |
| `image_arn must be an AWS Lambda MicroVM image ARN` | Use `arn:aws:lambda:<region>:<account>:microvm-image:<name>` |
| VPC profile validation fails | Set `egress_network_connector_arns` when `egress_mode` is `vpc` |
| `AccessDenied` on `RunMicroVM` | Attach the control-plane policy to the AWS identity running Cognition |
| `AccessDenied` on `iam:PassRole` | Add the agent execution role ARN to the allowed role list |
| Auth token creation fails | Allow `lambda:CreateMicroVMAuthToken` for approved MicroVM and image resources |
| `/healthz` fails | Confirm the image starts the runtime command server on the profile `port` |
| Commands hang | Check runtime server logs, command timeout, and network connector reachability |
| `SANDBOX_QUOTA_EXCEEDED` | Raise or relax profile `quota`, delete/abort/expire idle sessions, or wait for start history to age out |
| CloudWatch cost is high | Disable runtime logging by default or reduce log volume and retention |

## Debug Checklist

1. Confirm `/capabilities` includes `aws_lambda_microvm`.
2. Confirm `GET /sandbox/profiles/{name}` returns the expected image, region,
   role, networking, idle policy, logging, and quota settings.
3. Confirm the agent has the expected `sandbox_profile` and optional
   `sandbox_execution_role_arn`.
4. Watch `sandbox_lifecycle` SSE events for `provisioned`,
   `runtime_snapshot`, and teardown phases.
5. Inspect AWS service errors for control-plane permission, image, connector,
   and runtime health failures.

## Related

- [Setup](./setup.md)
- [IAM and Networking](./iam-and-networking.md)
- [Runtime Command Server](./runtime-command-server.md)
