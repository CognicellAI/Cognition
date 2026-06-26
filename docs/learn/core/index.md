# Core

Core turns the Foundations material into a small working backend. The endpoint
list matters less than the shape of the application: provider configuration,
session creation, streaming, tools, trusted scope, and runtime inspection.

## Audience

- Backend developers integrating Cognition into an application
- Builders learning the backend shape of agent applications
- Learners who completed Foundations and want to write code

## Prerequisites

- Python basics
- HTTP and JSON basics
- A local Cognition checkout
- Provider credentials for live model calls, or willingness to use mock/skipped
  checks where credentials are absent

## Outcomes

After Core, learners should be able to:

- Start a local agent backend.
- Configure an LLM provider through Cognition.
- Create a session and send a message through an API.
- Parse Server-Sent Events from a streamed agent response.
- Add or configure one useful tool.
- Use trusted scope headers and explain why the model should not supply its own
  authorization context.

## Planned modules

| Module | Output | Reference |
|---|---|---|
| Run an agent backend locally | Healthy local Cognition server | [Getting Started](/docs/guides/getting-started/) |
| Connect a model provider | Working provider config | [Configuration](/docs/guides/configuration/) |
| Create a conversation session | Session ID from the API | [API Reference](/docs/guides/api-reference/) |
| Stream agent progress | SSE parser that handles `token`, `tool_call`, `done`, and `error` | [Sessions & Messages](/docs/concepts/sessions-and-messages/) |
| Add one tool | Agent can call a simple support-style tool | [Extending Agents](/docs/guides/extending-agents/) |
| Carry trusted scope | Scoped request and isolation explanation | [Security](/docs/concepts/security/) |
| Inspect runs and events | Basic runtime visibility | [Observability](/docs/concepts/observability/) |

## Verification

Core lessons should prefer runnable checks:

- `curl` or Python `httpx` requests for API behavior.
- SSE parsing checks that fail clearly on malformed events.
- Scoped request checks that prove data is visible only under the expected
  effective scope.

## Reference links

- [Getting Started](/docs/guides/getting-started/)
- [API Reference](/docs/guides/api-reference/)
- [Configuration](/docs/guides/configuration/)
- [Extending Agents](/docs/guides/extending-agents/)
