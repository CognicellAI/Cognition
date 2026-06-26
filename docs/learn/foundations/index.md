# Foundations

Foundations introduces the ideas behind agent systems before any implementation
details. A student should leave this track able to discuss agents precisely:
what they are, how they act, what they need from a backend, and where they tend
to fail.

Cognition appears here only as an example system. The concepts come first.

## Audience

- New agent builders
- Product and platform teammates who need the vocabulary
- Developers who want the model before they write integration code

## Prerequisites

- Basic familiarity with APIs and JSON
- No Cognition experience required

## Outcomes

After Foundations, learners should be able to:

- Explain the difference between a chatbot and an agent.
- Describe the agent loop: model, tool call, observation, state update, next
  action.
- Place context, memory, tools, and evaluation in the agent architecture.
- Explain why production agents need backend infrastructure beyond a model call.
- Recognize where Cognition fits in a general agent-system architecture.

## Planned modules

| Module | Question | Exercise |
|---|---|---|
| Agents vs chatbots | What changes when a model can take actions? | Concept check |
| The agent loop | How does an agent decide, act, observe, and continue? | Diagram labeling |
| Tool design and tool calling | How should a model use external capabilities? | Tool classification |
| Context, state, and memory | What must persist across turns or sessions? | Scenario mapping |
| Action safety and sandboxing | What can go wrong when agents execute code? | Risk spotting |
| Streaming progress and observability | What should users and operators see while work is running? | Event matching |
| Multi-user isolation | How do agents avoid crossing user or tenant boundaries? | Scope matching |
| Evaluation and feedback | How do we know whether an agent is improving? | Rubric sketch |

## Reference links

- [Architecture](/docs/concepts/architecture/)
- [Agent Runtime](/docs/concepts/agent-runtime/)
- [Security](/docs/concepts/security/)
- [Observability](/docs/concepts/observability/)
