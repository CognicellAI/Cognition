# Agent Cards and Public Skills

An Agent Card is Cognition's public discovery contract for one exposed agent.
It describes identity, the preferred interface, protocol capabilities,
authentication requirements, supported media, and focused public skills. It
must not expose private runtime configuration.

## Identity and routing

`AgentDefinition.name` remains the stable runtime lookup key used by routes,
sessions, persistence, and deletion. `display_name` is the human-readable public
identity used for `AgentCard.name`.

When `a2a.public_interface_url` is configured, Cognition advertises that value
exactly in `supportedInterfaces`. Otherwise, it derives the per-agent
`/a2a/{name}` endpoint from the incoming request. The private runtime name does
not need to appear in a builder-provided public URL.

## Media modes

`defaultInputModes` and `defaultOutputModes` are MIME media types supported
generally by the agent. A skill's `inputModes` and `outputModes` override those
defaults for that skill.

Media modes are not A2A Part field names. For example:

```text
Part representation: raw
Media type:          image/png
```

The same `image/png` resource could instead be delivered through a `url` Part.
Builders should advertise a media type only when the full agent configuration
can reliably interpret or produce it. Safe transport and persistence do not by
themselves imply model understanding.

## Public skills versus runtime skills

`a2a.skills` contains public discovery descriptors. Root-level `skills` attaches
Cognition runtime instruction packages. They deliberately serve different
purposes:

| Public `a2a.skills` | Runtime `skills` |
|---|---|
| Stable external capability contract | Private implementation instructions |
| Published in the Agent Card | Loaded into the agent runtime |
| Includes descriptions, tags, examples, and MIME modes | References ConfigRegistry skill names |
| Must not reveal prompts, tools, or private topology | May coordinate private tools and workflows |

A public capability may be implemented by several private skills and tools. A
private runtime skill does not automatically represent an external contract.

When no public skills are configured, Cognition synthesizes one `primary` skill
from the public name, description, and default modes.

## Information that remains private

Generated cards do not publish:

- system prompts;
- runtime skill names or contents;
- tool names;
- subagent topology;
- scope values;
- credentials or secrets.

Agent Cards are public discovery documents. Sensitive capability details should
not be placed in descriptions, examples, tags, or authentication metadata.
