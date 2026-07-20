# A2A Message Parts

Cognition accepts every content variant defined by the A2A 1.0 `Part` message
without treating untrusted content as executable input. Parts inherit the exact
`effective_scope` of their enclosing A2A request, task, context, and session; a
caller cannot supply or override scope through Part metadata.

## Input contract

| A2A Part content | Cognition representation | Model-visible form |
|---|---|---|
| `text` | User-message text | Text, preserving Part order |
| `data` | Structured JSON value | A delimited JSON block, preserving structure |
| `raw` | Scoped, task-linked input artifact | An artifact reference with filename and media type |
| `url` | Scoped, task-linked URL artifact | A URL-artifact reference with filename and media type |

Mixed messages preserve Part order in the normalized user message. A message may
contain any combination of the four variants. Cognition rejects a Part whose A2A
content oneof is unset instead of silently dropping it.

`raw` contains inline bytes on the A2A wire. Cognition persists those bytes as a
base64-encoded artifact representation so they remain JSON-safe and can be
materialized through the existing scoped artifact backend. The normalized model
message contains a virtual `/artifacts/{id}` reference rather than the payload.

`url` remains a reference. Receiving a URL Part does **not** make Cognition fetch
the URL in the API process. An agent may use a builder-authorized tool or sandbox
workflow to retrieve it under the deployment's network, SSRF, size, and tool
policies.

## Scope and lifecycle

Part scope is inherited, not separately configured:

```text
trusted ingress effective_scope
  -> A2A task and context
    -> message
      -> normalized Parts
        -> task-linked input artifacts
```

Every derived artifact is persisted with the task's immutable
`effective_scope`, associated with the current run, and addressed by an opaque
server-generated ID. Artifact reads continue to require the same scope. IDs,
filenames, URLs, and Part metadata are never authorization inputs.

Message-id idempotency also covers derived input artifacts: retrying the same
message in the same agent and scope reuses the existing task rather than creating
another run or another set of artifacts.

## Execution boundary

A2A parsing, bounded normalization, and scoped persistence occur in the Cognition
server. They do not execute Part content. Text and data are normal model input.
Raw and URL Parts become inert artifact references.

Operations that interpret files, retrieve remote URLs, run programs, or transform
untrusted media remain explicit tool operations and use the configured sandbox or
builder service. Uploading a Part alone never executes it and never triggers a
network request.

## Agent Card modes

An A2A Part's content variant and its `mediaType` are separate concepts. Agent
Card input modes advertise MIME types, not the names `text`, `data`, `raw`, or
`url`.

The default card modes are `text/plain` and `application/json`: those are the
formats Cognition can place directly into model context for every agent. Builders
can override defaults and declare per-skill modes under `a2a`, but should advertise
additional MIME types only when the configured model or tools actually support
them end to end. Raw and URL Parts are accepted as attachment references without
implying that every selected model can interpret their media types. See the
[A2A Builder Guide](../../guides/a2a.md) for the public discovery contract.

## Failure behavior

Cognition returns an A2A content/parameter error before starting a run when:

- a Part has no content variant;
- structured data cannot be represented as JSON;
- raw content is malformed or exceeds configured request limits; or
- required Part fields are invalid under the A2A schema.

Unknown media types are retained as metadata for raw and URL artifacts. They are
not interpreted or executed by the adapter.
