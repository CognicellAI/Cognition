# Production

Production studies what changes when an agent becomes part of an application.
The first capstone is a Support Agent. Cognition provides the runtime, API,
streaming, persistence, scoping, sandboxing, and observability. The application
still owns the product rules, the data model, and the user experience.

The first version is API-only. A frontend can come later, after the backend
contract is clear.

## Audience

- Product builders designing an agent-backed workflow
- Backend engineers preparing to ship an agent system in an application
- Learners who completed Core and want a finished case study

## Prerequisites

- Completion of the Core path, or equivalent Cognition API familiarity
- Basic Python project structure
- Understanding of app-layer versus agent-backend responsibilities

## Outcomes

After Production, learners should be able to:

- Describe which responsibilities belong to Cognition and which belong to the
  embedding application.
- Build a support workflow over mock customers, plans, tickets, and notes.
- Demonstrate scoped session behavior.
- Show streamed output and persisted runtime state.
- Write a short architecture note with boundary decisions and verification
  output.

## Capstone: Support Agent

The Support Agent capstone starts with static fixtures under
`examples/learning/support-agent/`. Treat it as a small case study in agent
product design. The app layer owns the support workflow, ticket data, and
product policy. Cognition owns the agent runtime, API, streaming, persistence,
scoping, sandboxing, and observability.

Planned modules:

| Module | Output |
|---|---|
| Product problem and boundaries | Support workflow and responsibility decisions |
| Domain data fixtures | Customers, plans, tickets, and support notes |
| Agent interface contract | Prompt, tools, runtime settings, and scope expectations |
| Streaming API integration | Session lifecycle and streaming client |
| Tool design | Lookup ticket, summarize history, draft response |
| Tenant isolation | Tenant and end-user isolation |
| Runtime durability | Conversation continuity and run/event inspection |
| Operational visibility | Logs, events, traces, metrics, and debugging checklist |
| Agent evaluation | Lightweight quality and policy checks |
| Final write-up | Architecture note, verification output, and demo script |

## Reference links

- [Core vs App Layer](/docs/guides/core-vs-app-layer/)
- [Getting Started](/docs/guides/getting-started/)
- [Extending Agents](/docs/guides/extending-agents/)
- [Observability](/docs/concepts/observability/)
- [Security](/docs/concepts/security/)
