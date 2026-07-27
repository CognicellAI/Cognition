# ADR-0002: Curated OpenTelemetry Agent Tracing

**Status:** Accepted  
**Date:** 2026-07-26  
**Deciders:** Cognition maintainers  
**Supersedes:** Pre-release native-agent tracing mode design  
**Related roadmap/issue:** `ROADMAP.md` — curated Agent tracing and authoritative Usage Events

## Context

Cognition currently combines custom runtime spans, FastAPI
auto-instrumentation, LangChain/LangGraph auto-instrumentation, and optional
vendor-native tracing modes.

A local tool-using Agent turn produced 209 spans, including 172 middleware-hook
spans, and 742,119 bytes of span attributes. Prompt, response, system
instruction, and tool-definition content was repeated across the tree. The
custom HTTP span ended before the streamed response completed, and runtime
context used `thread_id` as `run_id` in several paths.

The same run also exposed a separate correctness problem: LangChain/provider
telemetry reported 17,722 input and 224 output tokens while Cognition's
text-derived Usage Event reported 34 and 24. Approximate usage must not be
presented to builders as authoritative.

Cognition needs a useful Agent trace and accurate builder usage without
becoming a vendor-specific observability system, billing service, or tenant
analytics control plane.

## Decision

1. Cognition emits one active `cognition.agent.run` application span for each
   durable run attempt. The span is the semantic root of a durable/background
   run trace and may link to ingress; when execution is strictly synchronous,
   it may instead be a child of the active REST or A2A server span.
2. LangChain's built-in LangSmith OpenTelemetry-only bridge owns the semantic
   Agent workflow tree, model spans, tool spans, and subagent spans. It uses
   Cognition's already-configured global `TracerProvider`, so the native
   LangChain/LangGraph tree inherits the active Cognition span and exits
   through the same curated OTLP pipeline. A separate LangGraph/GenAI trace for
   the same run attempt is a context-propagation regression, not an accepted
   topology.
3. Cognition adds custom spans only for timed boundaries not visible to Deep
   Agents: runtime resolution, sandbox acquire/release, external policy,
   callback delivery, and exceptional projection recovery.
4. Low-volume durable transitions, context decisions, tool-safety decisions,
   HITL decisions, and final usage summaries become timestamped events on the
   active run or relevant child span, not separate traces. High-volume or audit
   detail remains in exact-scoped durable events and trace-correlated logs.
5. The standard profile does not install a second set of framework middleware
   wrappers and excludes probe endpoints and ASGI send/receive spans. Native
   LangSmith spans that are parents of retained LangGraph/model/tool spans stay
   in the trace even when their names describe middleware hooks; removing them
   would create orphaned children. A deployment-wide debug profile may retain
   additional Cognition and framework internals.
6. Duplicate metric-adapter spans and standalone routine noise are filtered
   before the batch queue without removing ancestors from the native semantic
   tree. Cognition does not redact, mask, truncate, or suppress trace content.
7. OTLP is the only production semantic export contract. An operator-owned
   Collector routes the curated trace to MLflow, LangSmith, or another
   compatible destination.
8. Direct MLflow autologging and direct LangSmith export do not run beside the
   canonical pipeline because they can duplicate traces or bypass Cognition's
   root-run curation. The LangSmith OTel-only bridge is an in-process
   instrumentation adapter, not a destination: it emits through Cognition's
   global provider to the operator-owned Collector.
9. Cognition configures the OpenTelemetry `MeterProvider` used by the
   OpenLLMetry LangChain instrumentor. That instrumentor is installed only as
   a metrics adapter; its duplicate spans are discarded before the batch
   queue. It owns the standard model token and duration metrics, while
   Cognition defines no parallel token metric.
10. Builder-facing Usage Events are corrected in a companion runtime/API
    issue. They use only provider `AIMessage.usage_metadata`, explicitly
    represent complete, partial, or unavailable data, and never estimate
    tokens or cost. Standard `gen_ai.usage.*` attributes remain on model spans
    only. The Cognition root records its final run summary as a
    `cognition.usage.recorded` event and `cognition.usage.*` attributes so
    destinations such as MLflow do not count the same provider usage twice.
11. Request metrics/logging middleware remains pure ASGI so streaming duration
    covers the full response body, and Cognition does not create a duplicate
    custom HTTP span.
12. When a supported provider adapter can request streaming usage metadata
    safely, Cognition enables it. Missing provider metadata remains unavailable
    or partial; it is never replaced with estimates.

## Alternatives considered

### Keep broad auto-instrumentation and filter only in the Collector

Collector filtering and redaction happen in builder/operator-owned telemetry
infrastructure, which is the right place for destination-specific data policy.
Cognition still avoids installing duplicate hook wrappers in the standard
profile. It preserves any native intermediate span needed for valid parentage.

### Use vendor-native tracing as equal production modes

MLflow and LangSmith native modes can provide vendor-specific displays, but
they create different trace trees and privacy behavior. Supporting several
equal contracts increases configuration, testing, and duplicate-trace risk.
Standard GenAI attributes give MLflow and other OTLP backends enough
information to translate the canonical tree.

The LangSmith SDK's OTel-only bridge is not such a mode. It converts native
LangChain callback runs into OpenTelemetry spans on Cognition's existing
provider and does not select or contact a trace destination itself.

### Replace auto-instrumentation with fully manual spans

Manual spans would give Cognition complete control, but would duplicate
LangGraph's knowledge of models, tools, nodes, routes, and subagents. The
decision keeps auto-instrumentation for framework behavior and custom spans
only for Cognition-owned boundaries.

### Remove builder-facing Usage Events

Operators can inspect token telemetry in MLflow or Prometheus, but builders
also need a scoped, streaming run result for budgets, evaluation, and external
billing. Cognition retains that projection while removing all estimation.

## Consequences

### Positive

- Builders see one stable durable Agent run trace shape across REST, A2A, and
  background execution.
- The application span covers Cognition work before and after LangGraph while
  framework spans preserve the detailed execution tree beneath it.
- The useful LangGraph workflow remains visible without duplicate wrapper
  noise or orphaned child spans.
- Raw Agent trace content is available to builder back-office observability
  systems.
- MLflow remains supported through its standard OTLP/GenAI translation.
- Trace destinations can change without changing Agent definitions.
- Token metrics come from provider data and remain independent from trace
  sampling.
- Usage Events can be accurate without turning tracing into application state.

### Negative

- Cognition depends on two upstream adapters with deliberately separate jobs:
  LangSmith's OTel-only bridge for semantic spans and OpenLLMetry's LangChain
  instrumentor for standard GenAI metrics.
- Cognition must drop the OpenLLMetry adapter's duplicate spans before they
  enter the export queue and regression-test that the retained LangSmith spans
  remain directly parented beneath the Agent run.
- Cognition must keep the application span active across async streaming and
  test context propagation when LangGraph or its instrumentation changes.
- Raw traces may contain tenant conversation content, tool payloads, provider
  payloads, URLs, paths, or credentials surfaced by upstream instrumentation.
  Builders/operators must protect, redact, retain, and export those traces in
  their own observability pipeline.
- OTLP destinations may present the same semantic attributes differently.
- Usage Events require a companion runtime/API change and compatibility tests.
- Providers that do not report streaming usage produce partial or unavailable
  results; Cognition will not fabricate a fallback.

## Migration and rollback

This is a pre-release architecture change and requires no persistence
migration.

1. Add the Agent root and curation pipeline behind existing tracing enablement.
2. Keep `COGNITION_OTEL_ENDPOINT` as an alias while making
   `COGNITION_OTLP_ENDPOINT` canonical.
3. Remove the pre-release `COGNITION_NATIVE_AGENT_TRACING` destination switch.
   Enable LangSmith's internal OTel-only instrumentation bridge whenever
   canonical Cognition tracing is enabled.
4. Update the local Collector and MLflow stack so MLflow endpoint and
   experiment-id selection live in operator-owned Collector configuration.
5. Deliver authoritative Usage Events as a companion change after the trace
   contract is accepted, or in parallel when the tests keep the two contracts
   independently reviewable.

Rollback disables the new tracing path and restores the current generic OTLP
setup. Runtime execution and durable state must not depend on either tracing
or metrics availability.

## Verification

- Reference standard trace: at most 40 spans.
- Correct graph, model, tool, subagent, and middleware-decision structure.
- Exactly one semantic trace ID per run attempt across the Cognition,
  LangGraph, model, tool, and subagent spans.
- Semantic framework spans use the `langsmith` instrumentation scope;
  OpenLLMetry metric-adapter spans never reach OTLP.
- Probe endpoints, ASGI send/receive spans, and duplicate metric-adapter spans
  are absent from standard traces; every retained child has a retained parent.
- Agent root covers runtime resolution through terminal transition and sandbox
  teardown.
- Sampled run trace ID persists on the exact-scoped run.
- Automatic token metrics reach Prometheus independently of trace sampling.
- MLflow recognizes Agent, model, tool, model identity, and token attributes.
- MLflow trace-level token totals equal the provider model-span totals rather
  than summing a duplicate standard usage projection from the Agent root.
- Authoritative Usage Events agree with provider metadata and model spans for
  complete, partial, unavailable, multi-call, and subagent cases.
- The local Compose smoke stack proves MLflow trace ingestion, Prometheus
  token metrics, absence of duplicate autolog traces, and absence of
  middleware-hook flooding.
- Sink failure never changes the Agent result.

## Architecture model updates

- `ROADMAP.md`
- `docs/proposals/curated-opentelemetry-tracing.md`
- `docs/concepts/observability.md`
- `docs/architecture/08-deployment-and-operations.md`
- `docs/architecture/10-code-derived-risks.md`
- `docs/guides/configuration.md`
- `docs/guides/deployment.md`
- `docs/guides/api-reference.md`
- `localdocs/v0.13.0-observability-optimization-strategy.md`
