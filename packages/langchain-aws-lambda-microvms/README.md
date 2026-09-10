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

## Optional post-allocation initialization

Embedding applications can pass `runtime_initializer: Callable[[str], dict]`.
After allocation and AWS proxy authentication, the SDK calls it with the provider
MicroVM ID, sends its JSON object directly to `/run` in the image's
`{microvmId, runHookPayload}` envelope, then checks `/healthz` before admitting
commands or file transfers. The complete envelope is limited to 16 KiB.
The HTTP call has a 60-second timeout; the callback must bound its own I/O.

Use this for custom images requiring transient launch material. The image's
ordinary provider hook must support waiting for this initialization. The callback
is trusted application code, not Agent configuration or a model tool. Bind any
authorization context in the embedding application; the SDK does not infer scope
from the VM ID, issue credentials, or resolve secrets. Keep secrets out of the
ordinary `run_hook_payload`, which AWS receives during allocation.

Initialization occurs once per allocation, including across healthcheck retries.
The SDK does not retain the returned payload or include it in runtime metadata.
Failure closes the backend to commands and requests termination; failed or pending
teardown remains observable and retryable through `terminate()`. An ambiguous
initialization response is not retried with new material. There is no refresh or
expiry revocation guarantee. Applications own callback/client instrumentation and
must ensure it does not log payloads. Omitting the callback preserves existing
launch behavior. Declarative Cognition server wiring is not yet provided.
