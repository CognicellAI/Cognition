# langchain-aws-lambda-microvms

Deep Agents sandbox backend for AWS Lambda MicroVMs.

The backend launches a Lambda MicroVM from a prebuilt image, creates an
in-memory proxy auth token, and speaks to a command server inside the MicroVM.
It implements the Deep Agents `BaseSandbox` contract with:

- `execute()` via the runtime `/execute` route.
- `upload_files()` via `/upload` for workspace files.
- `download_files()` via `/download` for workspace files.
- `terminate()` via the Lambda MicroVM control plane.

V1 intentionally does not build or update MicroVM images.

## Optional OpenTelemetry

Install the `otel` extra to guarantee the OTel API is available. The embedding
application owns its tracer and meter providers, exporters, sampling and queues;
the SDK configures none of them and does not depend on Cognition.

The SDK emits content-free spans and the `lambda_microvm.operation.duration`
histogram in seconds. Its only metric dimensions are constant `operation` names
and `outcome` (`success` or `error`). Stages include lazy acquisition, initialization
lock wait, AWS launch/resume, running-state polling, authentication, readiness,
execute/upload/download, and termination. Command strings, file contents,
authentication tokens, endpoints and resource IDs are not recorded.

Metrics work independently of trace sampling when the caller installs a meter
provider. Missing OTel or exporter failures must not change sandbox results.
Normal Python context propagation applies: `asyncio.to_thread` preserves the
caller's context; custom thread executors should propagate it explicitly.
Health and command protocols remain unchanged. Mounted-workspace diagnostics
belong to the image builder, outside this SDK.
