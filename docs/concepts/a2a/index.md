# A2A in Cognition

Cognition implements A2A 1.0 as a protocol surface over the same durable agent
runtime used by its native APIs. A2A does not create a second execution engine:
Agent Cards, JSON-RPC requests, tasks, streaming events, and artifacts project
onto Cognition's existing agent, session, run, scope, and storage primitives.

## Implementation model

```text
A2A caller
  -> builder-owned gateway authentication and authorization
    -> trusted effective-scope headers
      -> Cognition A2A JSON-RPC adapter
        -> durable task and session runtime
          -> model, tools, sandbox, artifacts, and observability
```

Cognition owns protocol validation, Agent Card generation, durable execution,
streaming, persistence, and exact-scope isolation. Builders own identity,
authorization, route selection, and the mapping from authenticated claims to
trusted scope headers.

## Concepts

| Concept | What it explains |
|---|---|
| [Agent Cards and public skills](agent-cards.md) | Public identity, endpoints, MIME modes, skills, and the boundary between discovery and runtime configuration. |
| [Message Parts and artifacts](message-parts.md) | Text, structured data, inline bytes, URL references, persistence, and execution boundaries. |
| [Tasks and streaming](tasks-and-streaming.md) | Durable task identity, runs, contexts, continuation, cancellation, and event delivery. |
| [Security and scoping](security-and-scoping.md) | Trusted ingress, exact-scope isolation, authentication discovery, and builder responsibilities. |

For setup and invocation steps, use the [A2A Builder Guide](../../guides/a2a.md).
For exact endpoints and request bodies, use the
[API Reference](../../guides/api-reference.md#a2a-protocol).

## Supported surface

Cognition advertises the `JSONRPC` binding and A2A protocol version `1.0`.
It supports `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`,
`CancelTask`, and `SubscribeToTask`. Streaming is enabled. Push notifications,
gRPC, HTTP+JSON, and authenticated extended Agent Cards are not currently
exposed.
