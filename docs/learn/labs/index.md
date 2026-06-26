# Labs

Labs is for material that requires more judgment than the main sequence. Some
topics are stable but operationally difficult. Others are experiments. That
distinction matters, so every lab must carry a stability label.

Cognition supplies the concrete runtime for the labs, but each lab should teach
the agent-system pattern before it teaches the particular configuration.

## Audience

- Advanced agent builders
- Platform engineers
- Maintainers exploring future Cognition capabilities
- Learners who have completed Core or Production

## Prerequisites

- Comfort running Cognition locally
- Familiarity with sessions, streaming, tools, and scoping
- Willingness to treat experimental material as subject to change

## Stability labels

| Label | Meaning |
|---|---|
| `Stable` | Supported path appropriate for normal builders |
| `Advanced` | Supported path that requires deeper operational knowledge |
| `Experimental` | May change; not production guidance |

## Planned labs

| Lab | Stability | Output |
|---|---|---|
| Human-in-the-loop policy | Advanced | Approval and resume flow over a paused run |
| Delegated agent workstreams | Experimental | Async subagent experiment with explicit caveats |
| Evaluation orchestration | Experimental | Definition-driven evaluation sketch |
| Scope-aware memory | Experimental | Memory isolation exercise |
| External tool ecosystems | Advanced | Tool capability from an MCP server |
| Sandbox isolation | Advanced | Resource and filesystem isolation checks |
| Advanced observability | Advanced | Trace/event correlation walkthrough |
| Agent-to-agent exposure | Advanced | Agent card and protocol endpoint experiment |

## Lab rules

- Every lab must state its stability label.
- Experimental labs must explain what may change.
- Labs should be independently removable from the docs nav.
- Labs should not be prerequisites for Foundations, Core, or Production.

## Reference links

- [Agent Runtime](/docs/concepts/agent-runtime/)
- [Kubernetes Sandbox](/docs/concepts/kubernetes-sandbox/)
- [Observability](/docs/concepts/observability/)
- [API Reference](/docs/guides/api-reference/)
