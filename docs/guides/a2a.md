# Configure and Invoke an A2A Agent

This guide publishes one Cognition agent through A2A 1.0, configures its Agent
Card, and sends a message through the JSON-RPC endpoint.

For the implementation model and design boundaries, start with
[A2A in Cognition](../concepts/a2a/index.md).

## 1. Enable A2A

A2A routes are enabled by default. Ensure the deployment does not set:

```bash
COGNITION_A2A_ENABLED=false
```

This deployment setting only mounts the protocol surface. Each agent must also
opt in individually.

## 2. Configure the agent

All agent-level A2A settings live under `a2a`. `display_name` and `description`
remain at the root because they are general agent presentation fields.

```yaml
# .cognition/agents/document-agent.yaml
name: document-agent
display_name: Document Intelligence
description: Analyzes business documents.
mode: primary
a2a:
  exposed: true
  public_interface_url: https://agents.example.com/document-intelligence/a2a
  default_input_modes: [text/plain, application/json]
  default_output_modes: [text/plain, application/json]
  skills:
    - id: document-analysis
      name: Document Analysis
      description: Extracts and summarizes PDF documents.
      tags: [documents, pdf, extraction]
      examples:
        - Summarize the attached contract.
      input_modes: [application/pdf]
      output_modes: [text/plain, application/json]
system_prompt: |
  Analyze documents using the tools and policies available to you.
```

The same definition can be created through the API:

```bash
curl -X POST http://localhost:8000/agents \
  -H 'Content-Type: application/json' \
  -H 'X-Cognition-Scope-Account: acme' \
  --data @- <<'JSON'
{
  "name": "document-agent",
  "display_name": "Document Intelligence",
  "description": "Analyzes business documents.",
  "mode": "primary",
  "system_prompt": "Analyze documents using the tools and policies available to you.",
  "a2a": {
    "exposed": true,
    "public_interface_url": "https://agents.example.com/document-intelligence/a2a",
    "default_input_modes": ["text/plain", "application/json"],
    "default_output_modes": ["text/plain", "application/json"],
    "skills": [
      {
        "id": "document-analysis",
        "name": "Document Analysis",
        "description": "Extracts and summarizes PDF documents.",
        "tags": ["documents", "pdf", "extraction"],
        "examples": ["Summarize the attached contract."],
        "input_modes": ["application/pdf"],
        "output_modes": ["text/plain", "application/json"]
      }
    ]
  }
}
JSON
```

Use [Agent Cards and Public Skills](../concepts/a2a/agent-cards.md) to choose
accurate media modes and keep public capabilities separate from runtime skills.
The exact field contract is listed in the
[API Reference](api-reference.md#post-agents).

## 3. Configure authentication discovery

If a gateway protects the public endpoint, configure canonical A2A ProtoJSON:

```bash
COGNITION_A2A_SECURITY_SCHEMES='{"oauth2":{"oauth2SecurityScheme":{"flows":{"clientCredentials":{"tokenUrl":"https://auth.example.com/oauth/token","scopes":{"a2a.invoke":"Invoke the agent"}}}}}}'
COGNITION_A2A_SECURITY_REQUIREMENTS='[{"schemes":{"oauth2":{}}}]'
```

Cognition publishes this metadata; the gateway must enforce the matching
authentication policy. See [Security and Scoping](../concepts/a2a/security-and-scoping.md)
before exposing an agent publicly.

## 4. Retrieve the Agent Card

Use the same trusted scope used when the agent was created:

```bash
curl \
  -H 'X-Cognition-Scope-Account: acme' \
  http://localhost:8000/a2a/document-agent/.well-known/agent-card.json
```

Confirm that the response advertises the expected public name, interface URL,
MIME modes, skills, and authentication requirements.

## 5. Send a message

```bash
curl -X POST http://localhost:8000/a2a/document-agent \
  -H 'Content-Type: application/json' \
  -H 'A2A-Version: 1.0' \
  -H 'X-Cognition-Scope-Account: acme' \
  --data @- <<'JSON'
{
  "jsonrpc": "2.0",
  "id": "request-1",
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "message-1",
      "role": "ROLE_USER",
      "parts": [
        {
          "text": "Summarize this document.",
          "mediaType": "text/plain",
          "metadata": {"responseMediaType": "application/json"}
        },
        {"data": {"priority": 3}, "mediaType": "application/json"},
        {"url": "https://files.example.com/contract.pdf", "filename": "contract.pdf", "mediaType": "application/pdf"}
      ]
    }
  }
}
JSON
```

See [Message Parts and Artifacts](../concepts/a2a/message-parts.md) before
accepting files or remote references. URL Parts are stored as references and are
not downloaded automatically. Part and Message metadata are preserved as
untrusted application context; they never override Cognition scope or policy.

## 6. Stream a response

Send the same message with `method: SendStreamingMessage`. The response is an
A2A JSON-RPC event stream containing ordered task, status, message, and artifact
updates. Use `SubscribeToTask` to reconnect to an existing non-terminal task.

See [Tasks and Streaming](../concepts/a2a/tasks-and-streaming.md) for durable
identity, continuation, cancellation, and replay behavior.

### Durability and resource controls

Cognition coalesces model tokens into bounded artifact updates. Each update is
persisted before it is emitted, so a disconnected subscriber can replay ordered
updates through `SubscribeToTask`; disconnecting does not cancel execution.
Tune the deployment without changing Agent Cards:

| Environment variable | Default | Purpose |
|---|---:|---|
| `COGNITION_A2A_MAX_PARTS` | `64` | Maximum Parts in one inbound message |
| `COGNITION_A2A_MAX_MESSAGE_BYTES` | `16777216` | Aggregate decoded inbound bytes |
| `COGNITION_A2A_MAX_TEXT_PART_BYTES` | `2097152` | Maximum UTF-8 bytes in one text Part |
| `COGNITION_A2A_MAX_DATA_PART_BYTES` | `2097152` | Maximum canonical JSON bytes in one data Part |
| `COGNITION_A2A_MAX_RAW_PART_BYTES` | `10485760` | Maximum decoded bytes in one raw Part |
| `COGNITION_A2A_MAX_OUTPUT_ARTIFACTS` | `100` | Maximum distinct artifacts per execution |
| `COGNITION_A2A_MAX_OUTPUT_BYTES` | `16777216` | Aggregate output limit per execution |
| `COGNITION_A2A_STREAM_CHUNK_BYTES` | `4096` | Target size for durable text chunks |
| `COGNITION_A2A_STREAM_FLUSH_INTERVAL_SECONDS` | `0.25` | Maximum active-stream coalescing interval |
| `COGNITION_A2A_TERMINAL_TASK_TTL_SECONDS` | `0` | Terminal task retention; zero disables deletion |
| `COGNITION_A2A_CLEANUP_INTERVAL_SECONDS` | `3600` | Minimum cleanup interval per active agent/scope |
| `COGNITION_A2A_CLEANUP_BATCH_SIZE` | `100` | Maximum tasks removed per cleanup pass |
| `COGNITION_A2A_CLEANUP_GRACE_SECONDS` | `300` | Additional terminal-state safety window |

Cleanup runs opportunistically for exact agent/scope namespaces receiving A2A
traffic. It never deletes active tasks or unrelated data in a shared context.
After a retained task is deleted, its former message-id idempotency key may be
used for a new task.

## 7. Verify isolation

Before deployment, repeat discovery and task/artifact reads with a different
scope and verify that Cognition returns not found or an empty scoped collection.
Use the same authorized scope for creation, discovery, invocation,
continuation, listing, subscription, and cancellation.

## Troubleshooting

| Symptom | Check |
|---|---|
| Agent Card returns `404` | Confirm `a2a.exposed: true`, an eligible `primary` or `all` mode, visibility, and the exact scope headers. |
| Card advertises a private URL | Set `a2a.public_interface_url` to the externally routed JSON-RPC endpoint. |
| Media type is rejected | Confirm valid MIME syntax and that the card or selected public skill advertises the format. |
| Raw Part is rejected | Check `COGNITION_A2A_MAX_RAW_PART_BYTES` and base64 validity. |
| Retry returns invalid parameters | A `messageId` was reused with execution-relevant content that differs from the original request. |
| URL content is not available | URL Parts are references; provide an explicit authorized retrieval tool if remote fetching is required. |
| Authentication metadata is missing | Configure both security environment values and restart Cognition so startup validation runs. |

For complete JSON-RPC requests, responses, headers, and errors, see the
[A2A API Reference](api-reference.md#a2a-protocol).

## Conformance testing

Cognition validates the A2A adapter against the official
[`a2aproject/a2a-tck`](https://github.com/a2aproject/a2a-tck) at the revision
pinned in `.github/workflows/a2a-conformance.yml`. The official TCK checkout is
not patched. Transport selection is driven by the deterministic fixture's Agent
Card, as recommended by the TCK, instead of forcing a transport on the command
line.

Pull requests run the applicable `MUST` suite as a release-blocking gate.
Pre-release and release workflows run the full suite; `SHOULD` and `MAY`
results are reported for review but do not redefine Cognition's required
conformance surface.

The system under test is `tests/support/a2a_tck_sut.py`, a deterministic,
test-only Cognition agent definition. It exercises Cognition's real Agent Card,
JSON-RPC, persistence, task lifecycle, artifact, and streaming paths without
introducing model-provider or OAuth-gateway availability into protocol
conformance. It is not Stock Guru and is never mounted in the production
application.

Each run produces a versioned evidence archive containing the official HTML,
JSON, and JUnit reports, a redacted fixture log, a machine-readable manifest,
and `COGNITION-EXPLANATION.md`. Cognition's optional SendMessage idempotency
extension remains enabled and independently tested in production code; only
the deterministic TCK fixture disables it because official scenarios reuse
message IDs as test data.
