# Core vs App Layer

Cognition is a backend for agent applications, not the application itself.

Builders are often clear on what Cognition can do, but less clear on what they should still own in their product. This guide defines that boundary so teams do not duplicate backend concerns in the app layer or push product-specific UI concerns into Cognition.

---

## The Rule of Thumb

Use Cognition for agent infrastructure.

Use your application for product experience.

If a capability must be durable, policy-aware, scoped, resumable, observable, or shared across multiple clients, it usually belongs in Cognition.

If a capability is primarily about presentation, user interaction, workflow orchestration in your product, or domain-specific business rules, it usually belongs in your application.

---

## Responsibilities at a Glance

| Area | Cognition Core | Your App Layer |
|---|---|---|
| Agent execution | Runs the agent loop, tools, subagents, and model calls | Starts runs and reacts to streamed results |
| Sessions and memory | Persists sessions, messages, checkpoints, and scoped config | Chooses how sessions map to users, tasks, or UI routes |
| Streaming | Emits canonical SSE events (`token`, `tool_call`, `tool_result`, `delegation`, `usage`, `done`) | Renders those events into chat bubbles, activity timelines, spinners, and progress UI |
| Tooling | Hosts trusted tools, middleware, skills, prompts, and agent definitions | Decides which product workflows invoke which agents |
| Security boundaries | Enforces sandboxing, scope filtering, rate limiting, and API-side policy | Enforces end-user auth, permissions, billing, and product-level access control |
| Observability | Captures traces, metrics, and run-level backend telemetry | Tracks product analytics, UX funnels, retention, and user behavior |
| API contract | Provides HTTP endpoints and event protocol | Wraps Cognition in app-specific APIs, BFFs, or frontend adapters if needed |

---

## What Belongs in Cognition Core

Put a capability in Cognition when it should be consistent across every client and every product surface.

- Agent runtime concerns: prompt execution, tool calling, subagent delegation, sandboxed command execution, and stateful continuation
- Durable state: sessions, messages, checkpoints, scoped config, and any backend state required to resume a run safely
- Shared protocol concerns: streaming event format, retries at the infrastructure layer, model/provider resolution, and structured errors
- Operational controls: observability, rate limiting, scope isolation, tool policy, and deployment-time configuration
- Reusable agent assets: agent definitions, skills, middleware, and trusted tool integrations used by more than one application path

Good examples:

- adding a new tool that should be available to the agent across web, CLI, and internal workflows
- enforcing `X-Cognition-Scope-*` isolation so all clients inherit the same tenant boundary
- exposing a stable event like `tool_call` so every app can render tool activity consistently
- persisting checkpoints so a run can survive reconnects and server restarts

---

## What Belongs in the App Layer

Put a capability in your application when it is primarily about your product's user experience, domain logic, or composition around Cognition.

- UI rendering: chat threads, markdown display, code viewers, citations, diff views, progress indicators, and attachment previews
- Interaction design: composer behavior, optimistic messages, retry buttons, keyboard shortcuts, pagination, and navigation
- Product workflows: deciding when to create sessions, how to label them, when to switch agents, and which app actions trigger which Cognition calls
- Identity and business rules: user login, entitlements, roles, pricing plans, quotas, and app-specific authorization rules
- App analytics: feature usage, funnel tracking, session retention, and conversion events
- Domain orchestration outside the agent: approval flows, ticket creation, CRM writes, case management, or human handoff logic

Good examples:

- deciding whether a `tool_call` shows as a timeline row, a toast, or a collapsible panel
- mapping one Cognition session to a support ticket, notebook, or project workspace in your product
- transforming Cognition events into a Vercel Chat SDK-compatible message model for your frontend
- hiding advanced agent events from end users while showing them to internal operators

---

## A Useful Mental Model

Think in three layers:

1. Cognition Core is the agent backend
2. Your app server or frontend adapter is the integration layer
3. Your product UI is the user experience layer

That middle integration layer is often where builder confusion happens. It is normal to add an app-specific adapter that converts Cognition's native event stream into your preferred frontend model. That adapter is part of your application, not Cognition Core.

---

## How to Decide Where Something Goes

Ask these questions in order:

1. Does this need to be durable across reconnects, restarts, or multiple clients?
2. Does this need to be enforced consistently for every caller?
3. Is this part of agent execution rather than presentation?
4. Would multiple applications built on Cognition need the same behavior?

If the answer is mostly yes, it likely belongs in Cognition.

Then ask the inverse:

1. Is this mainly about UX or visual presentation?
2. Is this specific to one product, team workflow, or customer journey?
3. Would another builder reasonably want to implement this differently?

If the answer is mostly yes, it likely belongs in your app layer.

---

## Common Boundary Mistakes

### Treating Cognition like a UI framework

Cognition streams backend events. It should not own chat bubble composition, frontend state stores, or component behavior.

### Re-implementing backend state in the app

If your app is trying to become the source of truth for messages, checkpoints, tool state, or run lifecycle, you are likely duplicating Cognition's job.

### Pushing product policy into generic agent config

Business entitlements, workspace membership, and billing rules usually belong in your product backend. Cognition should receive already-authorized requests and enforce its own runtime boundaries on top.

### Flattening all events into plain text

If you throw away `tool_call`, `tool_result`, `delegation`, or `usage`, you lose much of the value of an agent backend. Preserve structured events internally even if your first UI only renders text.

---

## Example: Chat SDK-Style Application

If you build a chat application on top of Cognition:

What Cognition should own:

- creating and persisting sessions
- executing the agent and tools
- streaming structured events
- enforcing scope and runtime policy
- storing durable message history and checkpoints

What your app should own:

- converting streamed events into UI messages and parts
- deciding how tool activity appears in the interface
- user authentication and app permissions
- app-specific message metadata such as pinned threads, inbox state, labels, or customer records
- any framework-specific adapter for Vercel AI SDK, mobile chat UI, or internal design system components

This is the right pattern: Cognition as the backend, your app as the product.

---

## Builder Checklist

Before adding a feature, check the boundary:

- Put it in Cognition if it affects execution correctness, durability, isolation, or shared backend behavior
- Put it in your app if it affects UI, workflow composition, or product-specific policy
- Add an adapter layer if Cognition events need to be transformed for a frontend framework
- Keep Cognition's native event model intact even when you expose a simplified app-facing shape

---

## Related Documents

- [Architecture](../concepts/architecture.md) for the 7-layer model inside Cognition
- [Getting Started](./getting-started.md) for the basic HTTP integration flow
- [API Reference](./api-reference.md) for routes, SSE events, and scoping headers
- [Extending Agents](./extending-agents.md) for tools, skills, middleware, and agent definitions
