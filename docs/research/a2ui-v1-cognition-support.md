# A2UI v1.0 over A2A: Research for Cognition Support

**Research date:** 2026-08-16

**Scope:** Official A2UI v1.0 A2A extension and the A2A 1.0 protocol

**Status warning:** A2UI v1.0 is still a **release candidate** and a living
specification. The current production A2UI release is v0.9.1. This note pins
its A2UI findings to commit
[`44a420b`](https://github.com/a2ui-project/a2ui/tree/44a420b67957fafc0b02d55a153fdaf72e32ffb5)
and its A2A findings to commit
[`134a382`](https://github.com/a2aproject/A2A/tree/134a382ed38a0c527902e21b5b61c1666a60402e).
The official A2UI repository explicitly says to expect changes before v1.0
GA. [A2UI project status](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/README.md#L13-L19)

## Executive conclusion

Cognition can support A2UI v1.0 as an **optional capability on each A2A
agent**, without becoming an A2UI renderer and without maintaining application
schemas. The agent configuration should identify the A2UI version and catalogs
that the agent can generate. Cognition should then handle discovery,
negotiation, validation, typed runtime delivery, and A2A wire projection.

The canonical extension URI is:

```text
https://a2ui.org/a2a-extension/a2ui/v1.0
```

An enabled Cognition agent should advertise that URI with `required: false` and
its catalog capabilities. A disabled agent should neither advertise nor
activate A2UI. A2UI output is an A2A structured-data `Part` whose data is an
**array** of complete A2UI messages and whose media type is
`application/a2ui+json`. Text and A2UI data may coexist in the same A2A message
or artifact because A2A containers hold repeated heterogeneous `Part` values.
[A2UI extension](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/extensions/a2a/docs/a2ui_extension_specification.md)
[A2A `Part`, `Message`, and `Artifact`](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/specification/a2a.proto#L221-L295)

The v1.0 candidate A2UI binding still contains pre-A2A-1.0 examples. Cognition
already uses `a2a-sdk>=1.0.3`, so it should preserve the normative A2A 1.0 wire
shape and add only narrowly scoped compatibility metadata where needed. The
official A2UI Python agent SDK cannot currently be adopted directly because it
requires `a2a-sdk>=0.3.0,<0.4.0`.
[A2UI Python package dependency](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/agent_sdks/python/a2ui_agent/pyproject.toml#L19-L27)
[A2A v1 migration: unified Part](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/docs/whats-new-v1.md#part-structure-unification)

## Exact wire contract

### Extension identity and Agent Card

The URI includes the exact A2UI schema version. Requesting v1.0 does not imply
support for another version. The A2A extension guide requires a new URI for a
breaking extension change and forbids silently falling back to a different
version. [A2UI extension URI](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/extensions/a2a/docs/a2ui_extension_specification.md#extension-uri)
[A2A extension versioning](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/docs/topics/extensions.md#implementation-considerations)

The A2UI candidate encourages, but does not require, advertisement in
`AgentCard.capabilities.extensions`. For Cognition, advertisement should be
required whenever the per-agent A2UI option is enabled so clients can discover
the capability deterministically. The declaration is:

```json
{
  "uri": "https://a2ui.org/a2a-extension/a2ui/v1.0",
  "description": "Provides agent-driven UI using A2UI v1.0.",
  "required": false,
  "params": {
    "supportedCatalogIds": [
      "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json"
    ],
    "acceptsInlineCatalogs": false
  }
}
```

`supportedCatalogIds` identifies catalogs the agent can generate; catalog IDs
are identifiers and are **not necessarily resolvable URLs**.
`acceptsInlineCatalogs` is optional and defaults to `false`. The extension page
and JSON Schema make `supportedCatalogIds` optional, while the base protocol
prose calls it required. Cognition should resolve that candidate inconsistency
conservatively by requiring at least one configured catalog whenever A2UI is
enabled. [Agent Card declaration](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/extensions/a2a/docs/a2ui_extension_specification.md#agent-card)
[Agent capabilities schema](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/json/agent_capabilities.json)
[A2UI capability prose](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/docs/a2ui_protocol.md#agent-capabilities)

`application/a2ui+json` should also appear in the enabled agent's applicable
A2A input/output modes. It is media negotiation, not the A2UI activation
signal. In particular, the A2UI specification warns against using the
non-media token `accepted_output_modes: ["a2ui"]` as a trigger.
[A2UI activation note](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/extensions/a2a/docs/a2ui_extension_specification.md#a2a-extension-activation)
[A2A media modes](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/specification/a2a.proto#L380-L391)

### Negotiation and activation

The published A2UI v1.0 candidate defines two ways to negotiate use:

1. A renderer sends `message.metadata["a2uiRendererCapabilities"]`, containing
   its v1.0 catalogs and optional inline catalogs. The overview says this is
   attached to every renderer message.
2. A renderer may explicitly activate the extension through A2A's
   transport-defined extension mechanism.

The A2A 1.0 mechanism is the `A2A-Extensions` service parameter. HTTP-based
bindings carry it in the `A2A-Extensions` request header; gRPC carries it in
gRPC metadata. On explicit activation, the response should list successfully
activated extensions in `A2A-Extensions`. Unsupported optional extensions may
be ignored. [A2UI negotiation](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/extensions/a2a/docs/a2ui_extension_specification.md#a2a-extension-activation)
[A2A activation](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/docs/topics/extensions.md#extension-activation)
[A2A service parameters](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/docs/specification.md#326-service-parameters)

The A2UI page's `X-A2A-Extensions` examples are legacy and conflict with A2A
1.0's registered `A2A-Extensions` name. Cognition should accept the canonical
A2A 1.0 parameter. Supporting the `X-` spelling may be a temporary
compatibility option, but it must not be emitted as the canonical form.
[A2A HTTP header registration](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/docs/specification.md#1422-a2a-extensions-header)

There is active candidate churn: open A2UI PR
[#2033](https://github.com/a2ui-project/a2ui/pull/2033) proposes removing
per-request A2A extension activation from v1.0 and retaining only Agent Card
advertisement plus message-metadata negotiation. It is not merged and is not
current normative text, but it makes the activation layer a deliberate
compatibility boundary in Cognition rather than something to spread through
the runtime.

### Renderer metadata

The renderer sends:

- `message.metadata["a2uiRendererCapabilities"]`, validated against
  `renderer_capabilities.json`. Its `"v1.0".supportedCatalogIds` is required;
  `inlineCatalogs` is optional and should only be sent when the agent advertises
  `acceptsInlineCatalogs: true`.
- `message.metadata["a2uiRendererDataModel"]`, validated against
  `renderer_data_model.json`, on every renderer-to-agent message for a surface
  created with `sendDataModel: true`. It contains `version: "v1.0"` and a map
  of surface IDs to current data models.

[Renderer capabilities schema](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/json/renderer_capabilities.json)
[Renderer data model schema](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/json/renderer_data_model.json)
[A2A metadata mapping](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/extensions/a2a/docs/a2ui_extension_specification.md#a2a-renderer-to-agent-metadata)

### Parts and media type

The A2UI extension requires a structured-data A2A `Part` in both directions:

- Agent to renderer: `data` validates against
  `agent_to_renderer_list.json`.
- Renderer to agent: `data` validates against
  `renderer_to_agent_list.json`.
- `data` MUST be an array, even for one A2UI envelope.
- Envelopes carry `version: "v1.0"` and exactly one directional message type.
- The A2UI media type is `application/a2ui+json`.

[A2UI data encoding](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/extensions/a2a/docs/a2ui_extension_specification.md#data-encoding)
[Agent-to-renderer list schema](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/json/agent_to_renderer_list.json)
[Renderer-to-agent list schema](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/json/renderer_to_agent_list.json)

For A2A 1.0, the canonical JSON shape is:

```json
{
  "data": [
    {
      "version": "v1.0",
      "createSurface": {
        "surfaceId": "main",
        "catalogId": "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json"
      }
    }
  ],
  "mediaType": "application/a2ui+json"
}
```

A2A 1.0 has no `kind: "data"`; `data` itself selects the `Part` variant, and
`mediaType` is a first-class field. The A2UI candidate instead describes
`metadata.mimeType` and shows legacy `kind`. For compatibility with current
A2UI renderers, Cognition may temporarily duplicate the MIME marker as
`metadata.mimeType` while always emitting the canonical A2A 1.0 `mediaType`:

```json
{
  "data": [{"version": "v1.0", "createSurface": {"surfaceId": "main"}}],
  "mediaType": "application/a2ui+json",
  "metadata": {"mimeType": "application/a2ui+json"}
}
```

That duplication is a Cognition compatibility policy, not an A2A 1.0
requirement. [A2A v1 `Part`](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/specification/a2a.proto#L221-L242)
[A2A v1 migration examples](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/docs/specification.md#appendix-a-migration-guide-from-v03x-to-v10)

A2A permits Parts in both direct `Message` responses and task `Artifact`
outputs. A2UI does not require one container over the other. Progressive or
multi-event rendering should use the task/artifact stream; a direct-message
stream contains exactly one `Message` and then closes.
[A2A streaming response patterns](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/docs/specification.md#312-send-streaming-message)

### Streaming and ordering

A2UI is transport-independent but requires ordered delivery, message framing,
and metadata support. The downstream rendering flow is unidirectional; a
return channel is optional for static UI and required for actions or function
calls. The transport signals the end of an agent turn.
[A2UI transport contract](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/docs/a2ui_protocol.md#transport-decoupling)

Within each A2UI Data Part:

- messages are processed sequentially;
- the array is not a transaction;
- a receiver MUST continue after one message fails validation or application;
- the receiver SHOULD report that message's error;
- a renderer SHOULD defer repainting until the batch is processed to avoid
  flicker.

[A2UI batch processing rules](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/extensions/a2a/docs/a2ui_extension_specification.md#processing-rules)

For Cognition, each streamed A2A artifact update should therefore contain one
complete, validated A2UI array. Later batches may use the same artifact ID with
A2A's `append: true`; `lastChunk` and the terminal task status delimit the A2A
artifact/task lifecycle. Cognition must not expose partial JSON or split an
individual A2UI envelope across Parts.
[A2A artifact update semantics](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/specification/a2a.proto#L308-L324)

## Responsibilities

### Cognition agent/server

For an enabled agent, Cognition should:

1. Advertise the v1.0 extension and configured catalog IDs with
   `required: false`.
2. Parse canonical A2A activation and renderer metadata without flattening
   structured Parts into prompt text.
3. Compute the usable catalog set from agent and renderer capabilities.
4. Give the runtime the chosen catalog/schema as a request-scoped structured
   output contract; Cognition does not need an application-schema registry.
5. Validate every outbound batch against the agent-to-renderer list schema and
   the selected catalog before emitting a typed data artifact.
6. Validate inbound actions, errors, function calls, and renderer state against
   the renderer-to-agent and metadata schemas.
7. Preserve A2A ordering, IDs, scope, persistence, observability, and replay.
8. Execute `callAgentFunction` only through explicitly registered,
   policy-authorized agent capabilities; unknown or invalid calls return an
   A2UI `agentFunctionResponse` error with the original `functionCallId`.

A2UI's recommended generation loop is prompt, generate, validate, and retry
with validation feedback. Function call IDs and execution boundaries are
validated at runtime against the active catalog.
[Prompt-generate-validate loop](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/docs/a2ui_protocol.md#usage-pattern-the-prompt-generate-validate-loop)
[Function boundaries](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/docs/a2ui_protocol.md#callrendererfunction)

### Renderer/client

The renderer owns UI execution. It should:

1. Discover the Agent Card and send its v1.0 catalog capabilities.
2. Parse only A2UI-marked data Parts and validate each directional envelope.
3. Resolve abstract components through locally registered, trusted catalogs.
4. Apply messages in order, maintain surfaces and local data models, and
   render progressively.
5. Send actions, function responses, and errors as renderer-to-agent A2UI
   arrays.
6. Include current surface data in A2A message metadata only when the surface
   requested `sendDataModel: true`.
7. Enforce renderer-function boundaries, sanitization, and user-activation
   requirements.

The renderer, not Cognition, maps A2UI components to React, Flutter, native, or
other concrete widgets. [A2UI architecture](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/README.md#architecture)

## Normative versus optional behavior

The A2A specification defines RFC 2119 keywords and identifies `a2a.proto` as
the normative data model. The A2UI candidate uses uppercase conformance terms
and machine-readable JSON Schemas but does not include an equivalent RFC 2119
declaration. For implementation, Cognition should treat A2A 1.0 as authoritative
for the outer wire envelope and the pinned A2UI JSON Schemas plus explicit
uppercase requirements as authoritative for A2UI content.
[A2A conformance language](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/docs/specification.md#12-normative-references)
[A2A normative proto](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/docs/specification.md#11-specification-authority)

| Behavior | Status |
|---|---|
| Enable A2UI on a Cognition agent | Optional per agent |
| Advertise A2UI when enabled | Encouraged by A2UI; should be required by Cognition |
| Mark the Agent Card extension `required: false` | Required by the proposed optional-agent policy |
| Explicit A2A extension activation | Optional in current A2UI candidate; under active reconsideration |
| Renderer `a2uiRendererCapabilities` | Negotiation mechanism; its v1.0 catalog list is schema-required when present |
| Inline catalogs | Optional; accepted only if agent advertises support; default false |
| Renderer data-model synchronization | Optional per surface through `sendDataModel`; default false |
| `application/a2ui+json` structured-data Part | Required for A2UI payloads |
| Data value is an array of complete envelopes | Required |
| Directional schema and selected catalog validation | Required for conformant output/input |
| Sequential processing and continue after one invalid item | Required |
| Deferred repaint for a batch | Recommended |
| Bidirectional return channel | Optional for static UI; necessary for interactive actions/functions |
| Mixed text and A2UI Parts | Allowed by A2A; not prohibited by A2UI |
| A2A Message/Artifact `extensions` URI list | Available for provenance; not required by A2UI v1 candidate |

## Security considerations

1. **Treat all extension input as untrusted.** A2A requires rigorous validation
   of extension data and the same authentication/authorization controls as
   core methods. A2UI Parts, capability metadata, data models, inline catalogs,
   actions, and function results need size, depth, count, and schema limits.
   [A2A extension security](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/docs/topics/extensions.md#implementation-considerations)
2. **Do not fetch catalog IDs automatically.** A2UI explicitly defines them as
   identifiers, not necessarily resolvable URIs. Automatic remote `$ref` or
   catalog resolution would create an unnecessary SSRF and supply-chain
   surface. Bundle or builder-register trusted catalogs; validate inline
   catalogs only when explicitly enabled.
3. **Keep inline catalogs off by default.** If enabled, validate them against
   `catalog_definition.json`, bound their size and schema complexity, and do
   not treat catalog instructions as trusted system policy.
4. **Never execute generated code.** A2UI is declarative. Renderers allowlist
   trusted components, validate properties, and sanitize agent-provided text.
   [A2UI security model](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/README.md#high-level-philosophy)
   [Catalog security](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/docs/public/guides/defining-your-own-catalog.md#security-considerations)
5. **Constrain function execution.** Resolve functions only from the active,
   trusted catalog; enforce `callableFrom` and `requiresUserActivation`; reject
   unknown or wrong-boundary calls. Cognition must not translate arbitrary
   `callAgentFunction` names into unrestricted model tool calls.
6. **Preserve Cognition scope.** Renderer data models and action context can
   contain sensitive user data. Persistence, replay, logs, traces, and function
   dispatch must retain the request's trusted `effective_scope`; logs should
   record IDs and validation outcomes rather than full UI state by default.
7. **Apply normal A2A transport controls.** Production endpoints use TLS,
   authenticate and authorize every operation, validate parameters, and apply
   message-size and request-complexity limits.
   [A2A security requirements](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/docs/specification.md#13-security-considerations)

## Architectural consequences for the Cognition proposal

Cognition already has the right transport primitive:
`ArtifactEvent(kind="data", value=..., media_type=...)` maps to an A2A data
artifact. A2UI should specialize that path, not replace it.

The smallest complete architecture is:

```text
Per-agent A2UI config
  -> Agent Card extension + application/a2ui+json modes
  -> request negotiation and renderer metadata validation
  -> request-scoped catalog/schema generation contract
  -> typed A2UI batch event
  -> A2UI schema + selected catalog validation
  -> A2A 1.0 Artifact Part(data=[...], mediaType=application/a2ui+json)
```

Recommended module boundaries:

- **Agent definition:** optional A2UI v1 config, catalog IDs, inline-catalog
  policy, and optional trusted agent-function handlers.
- **A2A card/ingress:** declaration, canonical activation, capability parsing,
  and activated-extension response metadata.
- **Agent runtime:** protocol-neutral typed structured-output event carrying a
  complete A2UI batch; no A2A SDK types.
- **A2UI profile adapter:** pinned schemas, catalog resolution from trusted
  config, directional validation, MIME/provenance metadata, and compatibility
  handling.
- **A2A egress:** existing data-artifact projection plus A2A 1.0 media type and
  streaming semantics.
- **Observability:** activation, negotiated version/catalogs, validation
  outcomes, batch counts, and rejection reasons without logging sensitive
  payloads.

Do not make generic JSON-looking model text become A2UI. The runtime must emit
an explicitly typed, validated A2UI result. Do not add an application schema
registry: catalogs are part of the A2UI contract and remain per-agent or
request-scoped.

## Candidate-spec issues to track before claiming v1.0 conformance

1. The published A2UI page uses `X-A2A-Extensions`; A2A 1.0 specifies
   `A2A-Extensions`.
2. A2UI examples use legacy `kind: "data"` and `metadata.mimeType`; A2A 1.0
   removed `kind` and added `Part.mediaType`.
3. A2UI capability prose says `supportedCatalogIds` is required, while its
   extension text and schema do not require it.
4. The current A2UI Python A2A adapter targets `a2a-sdk<0.4` and its helper
   emits one object per Data Part, while the v1 extension now requires an array.
   [A2UI Part helper](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/agent_sdks/python/a2ui_agent/src/a2ui/a2a/parts.py)
5. Open PR #2033 proposes eliminating explicit extension activation in v1.0.

Therefore, Cognition should pin the candidate schemas used by a release,
isolate compatibility behavior in one adapter, test both canonical A2A 1.0 and
the selected A2UI renderer, and avoid claiming final A2UI v1.0 conformance
until the specification reaches GA and these contradictions are resolved.

## Primary sources

- [Rendered A2UI v1.0 A2A extension specification](https://a2ui.org/specification/v1.0-a2ui-extension-specification/)
- [A2UI v1.0 extension source at researched commit](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/extensions/a2a/docs/a2ui_extension_specification.md)
- [A2UI v1.0 base protocol](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/docs/a2ui_protocol.md)
- [A2UI v1.0 JSON Schemas](https://github.com/a2ui-project/a2ui/tree/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/json)
- [A2UI v1.0 Basic Catalog](https://github.com/a2ui-project/a2ui/blob/44a420b67957fafc0b02d55a153fdaf72e32ffb5/specification/v1_0/catalogs/basic/catalog.json)
- [A2A 1.0 extension guide](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/docs/topics/extensions.md)
- [A2A 1.0 specification](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/docs/specification.md)
- [Normative A2A 1.0 protocol model](https://github.com/a2aproject/A2A/blob/134a382ed38a0c527902e21b5b61c1666a60402e/specification/a2a.proto)
