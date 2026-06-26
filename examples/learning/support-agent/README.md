# Support Agent Learning Scaffold

This example is the first Production capstone for the agent-development course.
It starts with the backend only. Learners build a small Python support agent
over Cognition before adding any frontend.

## Goal

Build a support workflow where an agent can inspect mock customer and ticket
data, draft a support response, preserve scoped conversation state, and stream
its progress through Cognition.

## Boundaries

The embedding application owns:

- Support workflow rules
- Customer, plan, and ticket data
- Product-specific response policies
- Any future UI

Cognition owns:

- Agent runtime
- REST API and streaming
- Persistence
- Scoping
- Tool execution
- Observability

## Scaffold

```text
examples/learning/support-agent/
├── README.md
├── app/
│   └── README.md
├── data/
│   ├── customers.json
│   ├── plans.json
│   └── tickets.json
└── verify/
    └── README.md
```

## First milestones

1. Explain the support-agent problem and backend boundaries.
2. Inspect the JSON fixtures in `data/`.
3. Build a Python client in `app/` that creates a Cognition session and streams
   a response.
4. Design support tools that read from the fixtures.
5. Verify that scoped requests cannot cross tenants.
6. Record the architecture decision, commands run, and verification output.

## Fixture tenants

The starter data includes two tenants:

- `acme`: production SaaS customer with an urgent billing/support escalation.
- `northstar`: separate tenant used for scope isolation checks.

Learners should never rely on model-provided tenant identifiers. The app should
pass trusted scope through `X-Cognition-Scope-*` headers.
