# Lambda MicroVM Lifecycle and Observability

Lambda MicroVM sandboxes are provisioned lazily. Conversation-only turns do not
launch a MicroVM; the first command or file operation does.

## Lifecycle

```mermaid
sequenceDiagram
    participant User
    participant Cognition
    participant AWS
    participant Runtime

    User->>Cognition: POST /sessions
    Cognition-->>User: session status idle
    User->>Cognition: POST /sessions/{id}/messages
    Cognition->>Cognition: run status active
    Cognition->>AWS: RunMicroVM(image, role, connectors)
    Cognition->>AWS: CreateMicroVMAuthToken(port)
    Cognition->>Runtime: GET /healthz
    Cognition->>Runtime: POST /execute
    Runtime-->>Cognition: command result
    Cognition-->>User: run done, session idle
```

The session remains reusable after a successful run. If the user returns later,
the next message creates a new run on the same session and thread. The live
MicroVM may still be warm, suspended, or already released by policy; Cognition
should provision as needed when the next sandbox tool call occurs.

## Cleanup Triggers

Cognition releases sandbox resources when the session is:

- deleted
- aborted
- failed
- expired
- otherwise explicitly cleaned up by the backend lifecycle

Successful run completion does not automatically terminate the sandbox.

## Lifecycle Events

The backend emits `sandbox_lifecycle` events. Lambda MicroVM metadata includes:

- `microvm_id`
- `endpoint`
- `profile`
- `image`
- `image_version`
- `status`
- `region`
- `port`
- `maximum_duration_seconds`
- `logging_mode`
- `quota`
- `execution_role_fingerprint`
- `correlation` with session id, run id, agent name, profile, scope keys, and a
  scope fingerprint

Proxy auth tokens and credentials are filtered before lifecycle events are
streamed or persisted.

## Cost Visibility

Cognition exposes profile settings and lifecycle snapshots. AWS remains the
source of truth for provider-side billing, CloudWatch logs, and MicroVM service
metrics. Use both:

- Cognition events to correlate agent/session/run behavior
- AWS metrics and logs to inspect service-side runtime cost and failures

## Related

- [Sandbox Profiles](./profiles.md)
- [Troubleshooting](./troubleshooting.md)
- [Observability](../../observability.md)
