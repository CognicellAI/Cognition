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
        {"text": "Summarize this document.", "mediaType": "text/plain"},
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
not downloaded automatically.

## 6. Stream a response

Send the same message with `method: SendStreamingMessage`. The response is an
A2A JSON-RPC event stream containing ordered task, status, message, and artifact
updates. Use `SubscribeToTask` to reconnect to an existing non-terminal task.

See [Tasks and Streaming](../concepts/a2a/tasks-and-streaming.md) for durable
identity, continuation, cancellation, and replay behavior.

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
| URL content is not available | URL Parts are references; provide an explicit authorized retrieval tool if remote fetching is required. |
| Authentication metadata is missing | Configure both security environment values and restart Cognition so startup validation runs. |

For complete JSON-RPC requests, responses, headers, and errors, see the
[A2A API Reference](api-reference.md#a2a-protocol).
