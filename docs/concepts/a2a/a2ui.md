# A2UI v1.0 Extension

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
