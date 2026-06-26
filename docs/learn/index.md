# Learn agent development

This section is a curriculum for building agents, not a product manual. The aim
is to teach ideas that remain useful when the tooling changes: how agents decide
what to do, how an application exposes tools, how state is carried, how users
see progress, how the backend enforces scope, and how teams evaluate behavior.

Cognition is the system used for the exercises. It gives the course a working
backend for API calls, streaming, persistence, tools, sandboxing, observability,
and scoped access.

Use this section when you want a sequence of lessons. Use Concepts, Guides, and
Blueprints when you already know the topic you need.

## Course tracks

| Track | Level | What it studies | Start here when |
|---|---:|---|---|
| [Foundations](foundations/index.md) | Beginner | Basic agent concepts | You are new to agents or need a common vocabulary |
| [Core](core/index.md) | Intermediate | A first agent backend | You want a practical Python/API path |
| [Production](production/index.md) | Advanced | Production agent workflows | You want to design and verify an agent-backed application |
| [Labs](labs/index.md) | Advanced / Experimental | Specialized agent-system patterns | You want material beyond the required path |

The tracks build on one another:

1. Foundations introduces the mental model.
2. Core turns the model into a working backend.
3. Production studies the responsibilities of a real application.
4. Labs keeps advanced and unstable topics out of the required path.

## Lesson pattern

Each lesson should begin with the general agent-development problem. Only after
that should it show how Cognition implements the idea.

| Layer | Lesson focus |
|---|---|
| Concept | What problem appears in agent systems? |
| Design | What can go wrong, and who owns each responsibility? |
| Cognition implementation | Which API, runtime, scope, tool, event, or persistence feature is used? |
| Verification | How do we know the behavior works and stays inside the correct scope? |

## Starting points

### New to agents

Start with [Foundations](foundations/index.md). Focus on the agent loop, tool
calling, context, state, safety, and evaluation before touching APIs.

### Building a first backend

Start with [Core](core/index.md). The lessons introduce backend agent patterns
and point to Getting Started, API Reference, Configuration, and Extending Agents
for implementation details.

### Designing a product workflow

Start with [Production](production/index.md). The first capstone is a Support
Agent exercise with mock support data and Python/API integration. The first
version has no frontend so the backend responsibilities are easier to see.

### Studying advanced patterns

Use [Labs](labs/index.md). Labs are labeled `Stable`, `Advanced`, or
`Experimental` so the course does not treat research material as beginner
guidance.

## How lessons are written

Every lesson should follow the [lesson template](lesson-template.md). Lessons
should be short enough to teach one idea well. When a lesson needs API or
architecture details, link to the product docs instead of repeating them.

## Related reference

- [Getting Started](/docs/guides/getting-started/)
- [Architecture](/docs/concepts/architecture/)
- [Core vs App Layer](/docs/guides/core-vs-app-layer/)
- [Extending Agents](/docs/guides/extending-agents/)
- [API Reference](/docs/guides/api-reference/)
