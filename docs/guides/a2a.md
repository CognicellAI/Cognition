# A2A Builder Guide

Cognition exposes selected agents as strict A2A 1.0 JSON-RPC servers. It owns
Agent Card generation, protocol validation, durable task execution, streaming,
and exact-scope isolation. The embedding application or gateway remains
responsible for authenticating callers and authorizing the scope supplied to
Cognition.

## Enable the protocol surface

A2A routes are enabled by default. Set `COGNITION_A2A_ENABLED=false` to prevent
the well-known discovery and per-agent JSON-RPC routes from being mounted.

Enabling the deployment surface does not publish every agent. Each eligible
agent must also opt in with `a2a.exposed: true`. Hidden agents and agents whose
mode is `subagent` are never exposed.

## Configure an A2A agent

All A2A-only agent configuration lives under the `a2a` key. `display_name` and
`description` remain general agent presentation fields and stay at the root.

```yaml
# .cognition/agents/document-agent.yaml
name: document-agent
display_name: Document Intelligence
description: Analyzes business documents.
mode: primary
a2a:
  exposed: true
  public_interface_url: https://agents.example.com/document-intelligence/a2a
  default_input_modes:
    - text/plain
    - application/json
  default_output_modes:
    - text/plain
    - application/json
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

The same contract is accepted by `POST /agents`:

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

### A2A configuration reference

| Field | Default | Contract |
|---|---|---|
| `exposed` | `false` | Publishes an eligible agent through A2A when `true`. |
| `public_interface_url` | `null` | Absolute HTTP(S) JSON-RPC URL advertised exactly in the Agent Card. Credentials and fragments are rejected. |
| `default_input_modes` | `text/plain`, `application/json` | Non-empty list of MIME media types accepted generally by the agent. |
| `default_output_modes` | `text/plain`, `application/json` | Non-empty list of MIME media types produced generally by the agent. |
| `skills` | `[]` | Public A2A capability descriptors. These are separate from Cognition runtime skills. |

Each public skill requires a unique `id`, human-readable `name` and
`description`, and at least one `tag`. `examples`, `input_modes`, and
`output_modes` are optional. When a skill declares modes, they override the
card defaults for that skill.

If `a2a.skills` is empty, Cognition synthesizes one `primary` skill from the
agent's public name, description, and default modes.

## Choose accurate media modes

Agent Card modes are MIME types such as `text/plain`, `application/json`,
`application/pdf`, or `image/png`. They are not A2A Part field names.

Advertise a media type only when the complete agent configuration can reliably
interpret or produce it. Cognition can safely receive and persist an
`image/png` attachment, but transport support alone does not mean that the
selected model or tools can understand the image.

Cognition validates MIME syntax and unique public skill IDs. Whether a model,
tool, or workflow actually fulfills the advertised capability remains the
builder's contract.

## Understand Parts and artifacts

A2A messages can mix four Part content variants in order:

| Part | Cognition behavior |
|---|---|
| `text` | Added directly to model context. |
| `data` | Added as a delimited JSON block. |
| `raw` | Stored base64-encoded as a scoped, task-linked artifact. |
| `url` | Stored as a scoped URL reference; never fetched automatically. |

Part representation and media type are independent. An image can be delivered
inline as `raw` or by reference as `url`, with `mediaType: image/png` in either
case. See [A2A Message Parts](../concepts/a2a-message-parts.md) for limits,
idempotency, persistence, and sandbox behavior.

## Discover and invoke an agent

Retrieve a specific card:

```bash
curl \
  -H 'X-Cognition-Scope-Account: acme' \
  http://localhost:8000/a2a/document-agent/.well-known/agent-card.json
```

Send a mixed-Part message:

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

The same trusted scope must be supplied when creating, discovering, invoking,
continuing, listing, or canceling scoped resources.

## Supported protocol surface

Cognition advertises the `JSONRPC` binding and A2A protocol version `1.0`.
Supported operations are:

- `SendMessage`
- `SendStreamingMessage`
- `GetTask`
- `ListTasks`
- `CancelTask`
- `SubscribeToTask`

Streaming is enabled. Push notifications, gRPC, HTTP+JSON, and authenticated
extended Agent Cards are not currently exposed.

## Authentication discovery

Configure `COGNITION_A2A_SECURITY_SCHEMES` and
`COGNITION_A2A_SECURITY_REQUIREMENTS` with canonical A2A ProtoJSON when a
gateway protects the public endpoint. Cognition validates and publishes that
metadata; the gateway must enforce the matching authentication policy.

Never put client secrets, bearer tokens, or private credentials in an Agent
Card. Cards are public discovery documents.

## Scope and security boundary

Cognition treats configured `X-Cognition-Scope-*` headers as trusted ingress.
It carries the exact effective scope across agent lookup, tasks, contexts,
sessions, runs, events, artifacts, continuation, subscription, and
cancellation. Cross-scope identifiers are reported as not found.

Builders own authentication, authorization, route selection, and the mapping
from authenticated claims to scope headers. Cognition does not own tenant,
organization, membership, role, billing, or entitlement models.

## Public versus runtime skills

`a2a.skills` describes public capabilities for discovery. The root-level
`skills` field attaches Cognition runtime instruction packages. Cognition never
publishes runtime skill names, prompts, tools, subagents, scope values, or
secrets in the Agent Card.

Keep these surfaces deliberately separate: a public capability can be backed
by several private runtime skills and tools, while a private runtime skill does
not necessarily represent a stable external contract.
