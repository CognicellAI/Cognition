# A2UI v1.0 Candidate Extension

Cognition can expose A2UI v1.0 as an optional capability on an individual A2A
agent. A2UI support is disabled unless the agent definition includes
`a2a.a2ui`; there is no deployment-wide implicit enablement.

```yaml
name: project-planner
system_prompt: Help the user plan and explain project work.

a2a:
  exposed: true
  a2ui:
    version: "1.0"
    catalogs:
      - basic
```

When enabled, Cognition adds the A2UI extension URI to that agent's Agent Card:

```text
https://a2ui.org/a2a-extension/a2ui/v1.0
```

The extension is advertised with `required: false`, the pinned Basic catalog
ID, and `acceptsInlineCatalogs: false`. Cognition also adds
`application/a2ui+json` to the agent's applicable A2A input and output modes.
Agents without `a2a.a2ui` publish unchanged Agent Cards and continue on the
ordinary conversational path.

## Negotiation

A request activates A2UI only for an enabled agent and only when the request
supplies the A2UI extension URI through the canonical A2A `A2A-Extensions`
service parameter or includes valid `message.metadata.a2uiRendererCapabilities`.
Cognition validates renderer capabilities, rejects inline catalogs, and selects
a catalog from the intersection of renderer-supported and agent-supported
catalog IDs.

Catalog IDs are treated as identifiers, not URLs to fetch at request time.
Cognition ships pinned A2UI v1.0 candidate schemas and the Basic catalog with
recorded digests.

## Output

A2UI output is produced through typed runtime structured output. Cognition does
not parse JSON-looking assistant text into A2UI data.

Validated A2UI output is emitted as an A2A artifact data Part:

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

The output artifact carries the A2UI URI in `artifact.extensions`. Cognition may
also emit ordinary text artifacts in the same task, so a conversational answer
and a renderable A2UI surface can be delivered together.

## Renderer Input

Renderer-to-agent A2UI data Parts are validated against the pinned
renderer-to-agent schema before model execution. Renderer actions, renderer
function responses, renderer errors, and synchronized data-model snapshots are
preserved as scoped canonical input.

Cognition does not create an Agent-function registry for A2UI. If a renderer
sends `callAgentFunction`, Cognition returns an explicit
`agentFunctionResponse.error` A2UI batch and does not invoke the model.

Existing MCP, tool policy, human approval, authorization, scope isolation, and
output limits remain authoritative.

## Validation and execution boundaries

The release pins upstream revision
[`44a420b`](https://github.com/a2ui-project/a2ui/tree/44a420b67957fafc0b02d55a153fdaf72e32ffb5).
Pair clients with these candidate schemas and catalog semantics; the version
string alone does not establish compatibility with a later candidate renderer.

Cognition applies the normal A2A Part byte/count limits before A2UI negotiation,
then bounds A2UI batches to 64 messages and nesting to 32 levels. Supplied
capability and data-model metadata must be objects. Inline catalogs are rejected
before catalog schema validation. Outbound catalog references must belong to the
negotiated set, including references inside components and function calls.
Application data-model values remain opaque data, not catalog identifiers.

Model providers receive a simplified generation schema and the pinned Basic
catalog as guidance. The generation schema is deliberately permissive enough to
represent different Basic control types; it is not the acceptance boundary.
Cognition validates the returned envelope against the full pinned schema before
emitting any UI artifact. Invalid output gets one repair attempt within the
original execution timeout, then fails explicitly. Initial execution and
human-approval resume use the same policy. Internal envelope JSON is never
published as an ordinary structured-data artifact.

The bundled asset bytes and digests remain unchanged. Python's regular-expression
engine cannot evaluate the upstream Unicode identifier property escapes, so the
validator uses an equivalent identifier-format check in its in-memory schema
view. Transport bindings remain in the A2A layer; schema loading and validation
live in the foundation package and use the standard OTel API.

A valid UI action is still untrusted input. Rendering an Approve button does not
authorize a business operation or replace Cognition's tool-approval mechanism.
Builders authorize the request scope and any resulting business action. Clients
continue actions with the original A2A `contextId`; each request gets a new task
and its own persisted artifacts. `GetTask` and replay retain the extension and
media type under the existing exact-scope access rules.

## Compatibility evidence and reproduction

On September 8, 2026, live OpenAI-compatible calls produced an initial status
panel, a real rendered button action, an updated visible surface in the same
conversation, and matching terminal retrieval:

| Model | Initial task | Action task | Observed display |
|---|---|---|---|
| Claude Sonnet 4.6 | `e8c8ab58-737f-5bc9-bdc2-bba002319ec7` | `5c72affc-a286-5872-91ba-575e4c49e06e` | Pending Approval → Approved |
| GPT-5.4 | `c1e05666-1000-57cd-926a-cbdee5ca9efc` | `24b0710a-4f43-5ca6-9161-e506207b2cbd` | Ready for approval → Approved |

These are individual interoperability checks, not reliability or latency
benchmarks. Earlier attempts exposed invalid model-generated properties and a
renderer limitation when replacing a root component's type. The generation
instructions now include the authoritative catalog and preserve component
identities during updates. Gemini 3 Flash Preview rejected the provider-facing
structured-output schema; that provider/model combination is not validated.

The optional renderer fixture uses pinned `@a2ui/lit` and `@a2ui/web_core` 0.10.3
widgets in JSDOM. Those packages expose a v0.9 engine. A test-only adapter handles
the shared v1 create/update shapes, then clicks the actual rendered button and
checks changed DOM text. This proves that bounded roundtrip, **not full v1
renderer conformance**, browser visual quality, or a WayPost A2UI renderer.
Cognition ships no renderer and has no Node dependency at runtime.

Run the protocol/model test with credentials supplied through your existing
secret-management mechanism:

```bash
export COGNITION_A2UI_LIVE_MODEL=anthropic/claude-sonnet-4.6
uv run pytest tests/e2e/test_a2ui_live_model_e2e.py -q
```

The test requires `COGNITION_OPENAI_COMPATIBLE_API_KEY` and optionally
`COGNITION_OPENAI_COMPATIBLE_BASE_URL`. To include DOM rendering, install the
isolated fixture dependencies (Node 24 or newer) and set its path:

```bash
renderer_dir=$(mktemp -d)
cat > "$renderer_dir/package.json" <<'JSON'
{"private":true,"dependencies":{"@a2ui/lit":"0.10.3","@a2ui/web_core":"0.10.3","jsdom":"29.1.1"},"overrides":{"@a2ui/web_core":"0.10.3"}}
JSON
npm install --prefix "$renderer_dir"
export COGNITION_A2UI_RENDERER_NODE_MODULES="$renderer_dir/node_modules"
uv run pytest tests/e2e/test_a2ui_live_model_e2e.py -q
```

Without that optional path, the test sends a synthetic renderer action. An
optional `COGNITION_A2UI_EVIDENCE_DIR` saves task IDs and UI messages locally;
keep these captures outside the release tree. The ordinary unit suite separately
covers invalid output, catalog isolation, Unicode identifiers, checkpointed
approval resume and shared timeout behavior without live credentials.
