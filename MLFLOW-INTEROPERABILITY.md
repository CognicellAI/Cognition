# MLflow GenAI Interoperability Guide

## Overview

Cognition and MLflow GenAI operate at different layers of the AI application stack and integrate natively. Cognition is the **execution plane** -- it runs autonomous agents, manages sessions, sandboxes tool execution, and streams results. MLflow GenAI is the **intelligence plane** -- it observes agent quality, evaluates systematically, versions prompts, and enables continuous improvement.

**MLflow already provides a first-party integration for LangChain Deep Agent** -- the exact framework Cognition is built on. This is not a theoretical integration; it is a named, documented integration in MLflow's official catalog alongside LangChain, LangGraph, CrewAI, AutoGen, and 20+ other frameworks. See: [MLflow Deep Agent Integration](https://mlflow.org/docs/latest/genai/tracing/integrations/listing/deepagent/).

Both systems are built on OpenTelemetry. The integration requires as little as one line of code.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    Your Application UI                        │
│              (BreachLens, DataLens, GeneSmith)                │
└──────────────────┬──────────────────────┬────────────────────┘
                   │ REST/SSE             │ Feedback
                   ▼                      ▼
┌──────────────────────────────────────────────────────────────┐
│                   Cognition Engine                            │
│                                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │ Agent Loop   │  │ Sandbox      │  │ Session Store    │    │
│  │ (deepagents) │  │ (Local/Docker)│  │ (SQLite/Postgres)│    │
│  └──────┬───────┘  └──────┬───────┘  └──────────────────┘    │
│         │                 │                                   │
│  ┌──────▼─────────────────▼──────┐                           │
│  │   mlflow.langchain.autolog()  │  ◄── Native Integration   │
│  └──────────────┬────────────────┘                           │
└─────────────────┼────────────────────────────────────────────┘
                  │ Traces, Metrics, Spans
                  ▼
┌──────────────────────────────────────────────────────────────┐
│                   MLflow GenAI Platform                       │
│                                                              │
│  ┌──────────┐  ┌────────────┐  ┌───────────┐  ┌──────────┐  │
│  │ Tracing  │  │ Evaluation │  │  Prompt   │  │ AI       │  │
│  │ (OTel)   │  │ (Scorers)  │  │ Registry  │  │ Gateway  │  │
│  └──────────┘  └────────────┘  └───────────┘  └──────────┘  │
│                                                              │
│  ┌──────────┐  ┌────────────┐  ┌───────────┐               │
│  │ Feedback │  │ Datasets   │  │ MLflow UI │               │
│  └──────────┘  └────────────┘  └───────────┘               │
└──────────────────────────────────────────────────────────────┘
```

---

## Native Deep Agent Integration

MLflow's Deep Agent integration means Cognition's core agent loop -- `create_deep_agent()` from the `deepagents` library -- is automatically instrumented. This is the same call Cognition uses at `server/app/agent/cognition_agent.py:124`.

From MLflow's documentation:

> Since Deep Agent is built on LangGraph, MLflow Tracing works out of the box via `mlflow.langchain.autolog()`. This automatically captures the entire agent execution including planning, tool calls, and subagent interactions.

### Enabling It

```python
import mlflow
mlflow.langchain.autolog()
```

One line. No changes to agent code, middleware, or tool definitions required.

### What It Captures Automatically

| Data | Source | Captured As |
| --- | --- | --- |
| Every LLM call | LangChain `ChatOpenAI` / `ChatBedrock` | MLflow LLM span with inputs, outputs, model name |
| Token usage per call | LangChain model response | `mlflow.chat.tokenUsage` span attribute |
| Total token usage per trace | Aggregated across all LLM spans | `mlflow.trace.tokenUsage` metadata field |
| Every tool call | deepagents tool dispatch | MLflow TOOL span with name, arguments, result |
| Planning steps | deepagents `write_todos` | Captured as part of the agent execution graph |
| Subagent spawning | deepagents subagent orchestration | Nested child traces |
| Execution latency | Per-span timing | Automatic duration on every span |
| Error states | Exceptions in LLM or tool calls | Span error status and messages |
| Full ReAct loop | LangGraph state machine | Nested span tree showing the complete reasoning chain |

### What Appears in the MLflow UI

With just `mlflow.langchain.autolog()` enabled, the MLflow UI at `http://localhost:5000` provides:

#### Experiment Overview

Three tabs with real-time analytics:

- **Usage** -- Request counts over time, latency distribution charts, error rate tracking, token usage trends. All populated automatically from Cognition agent sessions.
- **Quality** -- Scorer result summaries and per-assessment charts. Empty until evaluation scorers are configured (see Integration Stage 3). Charts are dynamically generated based on available assessments.
- **Tool Calls** -- Statistics cards showing total tool calls, average latency, success rate, and failed calls. Per-tool performance summary. Tool usage frequency and latency charts. Tool error rates.

#### Trace List

A searchable, filterable table of all agent traces:

- Trace ID and name
- Input/output preview (the user's message and the agent's final response)
- Status (success/error)
- Duration
- Token usage
- Tags and metadata

Supports bulk operations (delete, tag editing) and search by name, tags, or metadata.

#### Per-Trace Detail View

Click any trace to see the full span tree:

- **Nested span hierarchy**: ReAct loop -> LLM reasoning -> tool calls -> sub-operations
- **Input/output for every span**: What the LLM received, what it responded, what each tool was called with and returned
- **Token usage per LLM call**: Input tokens, output tokens, total tokens
- **Latency per span**: Duration waterfall showing where time was spent
- **Error details**: Full stack traces for failed spans

---

## Integration Stages

The integration is incremental. Each stage adds capability on top of the previous one.

### Stage 1: Auto-Instrumentation (Day 1)

**Effort:** ~5 lines of code. **Prerequisite:** MLflow Tracking Server running.

**Where:** `server/app/observability/__init__.py`, inside `setup_tracing()` at line 149.

```python
# server/app/observability/__init__.py -- addition to setup_tracing()

import mlflow

def setup_tracing(settings):
    # ... existing OTel setup ...

    # MLflow auto-instrumentation for Deep Agent / LangChain
    if settings.mlflow_enabled:
        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        mlflow.set_experiment(settings.mlflow_experiment_name)
        mlflow.langchain.autolog()

    # Existing LangChain instrumentor (for Jaeger/OTel)
    if LangchainInstrumentor:
        LangchainInstrumentor().instrument(tracer_provider=provider)
```

**Coexistence with existing tracing:** Cognition already sends traces to Jaeger via OpenTelemetry. MLflow Tracing is OTel-compatible, so both systems coexist. Jaeger handles operational monitoring (latency percentiles, error rates). MLflow handles GenAI-specific analysis (prompt quality, reasoning chains, tool selection patterns).

**MLflow UI at this stage:**
- Experiment Overview: Usage and Tool Calls tabs fully populated
- Trace list: All agent sessions visible, searchable, filterable
- Trace detail: Full span trees for every agent interaction
- Token tracking: Automatic per-call and per-trace token counts

---

### Stage 2: Agent Lifecycle Middleware (Week 1)

**Effort:** ~80 lines. **Prerequisite:** Stage 1 complete.

Cognition's `AgentMiddleware` protocol provides lifecycle hooks that wrap every LLM call and tool call. A custom `MLflowTracingMiddleware` follows the same pattern as the existing `CognitionObservabilityMiddleware` in `server/app/agent/middleware.py`.

**Where:** New file `server/app/agent/mlflow_middleware.py`, registered in `server/app/agent/cognition_agent.py` at lines 116-121.

**What it adds beyond auto-instrumentation:**
- Cognition-specific metadata: session ID, workspace path, sandbox configuration
- Agent reasoning step boundaries (before/after model calls)
- Provider fallback information (which provider was selected, how many attempts)

```python
# server/app/agent/mlflow_middleware.py

from langchain.agents.middleware.types import AgentMiddleware
import mlflow


class MLflowTracingMiddleware(AgentMiddleware):
    """Middleware that traces agent lifecycle events to MLflow."""

    @property
    def name(self) -> str:
        return "mlflow_tracing"

    async def awrap_model_call(self, request, handler):
        with mlflow.start_span(name="llm_call", span_type="LLM") as span:
            span.set_inputs({"messages": str(request)})
            response = await handler(request)
            span.set_outputs({"response": str(response)})
            return response

    async def awrap_tool_call(self, request, handler):
        with mlflow.start_span(name=request.tool_name, span_type="TOOL") as span:
            span.set_inputs({"arguments": str(request.tool_input)})
            result = await handler(request)
            span.set_outputs({"result": str(result)})
            return result
```

**Registration in the agent factory:**

```python
# server/app/agent/cognition_agent.py -- middleware stack assembly

agent_middleware = list(middleware) if middleware else []
agent_middleware.extend([
    CognitionObservabilityMiddleware(),
    CognitionStreamingMiddleware(),
    MLflowTracingMiddleware(),  # Add MLflow middleware
])
```

**MLflow UI at this stage:**
- Everything from Stage 1
- Additional span attributes with Cognition-specific context (session ID, workspace)
- Richer trace metadata enabling filtering by session, workspace, or provider

---

### Stage 3: Configuration (Week 1)

**Effort:** ~30 lines. **Prerequisite:** None (can be done alongside Stage 1).

Add MLflow settings to Cognition's configuration system so the integration is user-configurable.

**Settings:**

```python
# server/app/settings.py -- new fields after existing observability settings

# MLflow settings
mlflow_enabled: bool = Field(default=False, alias="COGNITION_MLFLOW_ENABLED")
mlflow_tracking_uri: Optional[str] = Field(
    default="http://localhost:5000",
    alias="COGNITION_MLFLOW_TRACKING_URI",
)
mlflow_experiment_name: Optional[str] = Field(
    default="cognition",
    alias="COGNITION_MLFLOW_EXPERIMENT",
)
```

**YAML configuration:**

```yaml
# .cognition/config.yaml

mlflow:
  enabled: true
  tracking_uri: "http://localhost:5000"
  experiment_name: "cognition"
```

**Environment variables:**

```bash
export COGNITION_MLFLOW_ENABLED=true
export COGNITION_MLFLOW_TRACKING_URI=http://localhost:5000
export COGNITION_MLFLOW_EXPERIMENT=cognition
```

**Dependencies:**

```toml
# pyproject.toml -- add to optional dependencies

[project.optional-dependencies]
mlflow = [
    "mlflow[genai]>=3.0.0",
]
```

For production deployments where only tracing is needed (no evaluation or prompt management), use the lightweight SDK:

```toml
mlflow-tracing = [
    "mlflow-tracing>=1.0.0",  # 95% smaller than full mlflow package
]
```

---

### Stage 4: Session-Level Experiment Tracking (Week 2)

**Effort:** ~20 lines. **Prerequisite:** Stage 3 complete.

Each Cognition session maps naturally to an MLflow run. Session metadata becomes run tags; session metrics become logged metrics.

**Where:** `server/app/api/routes/sessions.py` lines 61-101, at session creation time.

```python
# When a Cognition session is created:
run = mlflow.start_run(
    run_name=f"session-{session.id}",
    tags={
        "cognition.session_id": session.id,
        "cognition.title": session.title,
        "cognition.workspace": str(workspace_path),
        "cognition.model": settings.llm_model,
        "cognition.provider": settings.llm_provider,
    }
)
```

**As the session progresses, the run accumulates:**
- Traces from the auto-instrumentation and middleware (Stages 1-2)
- Metrics: total token usage, cost, tool call count, latency per turn
- Tags: session outcome, error states, number of turns

**MLflow UI at this stage:**
- Everything from Stages 1-2
- Traces grouped by session (via run context)
- Run comparison view: compare token usage, latency, and tool call patterns across sessions
- Tag-based filtering: find all sessions for a specific workspace, model, or provider

---

### Stage 5: Evaluation Pipeline (Month 1)

**Effort:** ~200 lines (new module). **Prerequisite:** Stages 1-2 complete (traces must exist).

Cognition has no evaluation capability. This is its biggest gap. MLflow GenAI's evaluation framework enables systematic quality assessment of agent sessions using the traces already being captured.

**Offline evaluation of agent sessions:**

```python
import mlflow

# Retrieve traces from completed Cognition sessions
traces = mlflow.search_traces(experiment_ids=["cognition"])

# Evaluate agent quality with built-in and custom scorers
results = mlflow.genai.evaluate(
    data=traces,
    scorers=[
        mlflow.genai.scorers.Correctness(),
        mlflow.genai.scorers.Helpfulness(),
        mlflow.genai.scorers.Safety(),
    ]
)
```

**Custom scorers for agent-specific quality:**

```python
from mlflow.genai.scorers import Scorer

class ToolEfficiencyScorer(Scorer):
    """Score whether the agent used tools efficiently."""

    name = "tool_efficiency"

    def score(self, *, inputs, outputs, traces):
        tool_calls = [s for s in traces.spans if s.span_type == "TOOL"]
        if len(tool_calls) > 10:
            return {"score": 0.5, "rationale": "Excessive tool usage"}
        return {"score": 1.0, "rationale": "Efficient tool usage"}


class SafetyComplianceScorer(Scorer):
    """Score whether the agent respected system prompt constraints."""

    name = "safety_compliance"

    def score(self, *, inputs, outputs, traces):
        tool_calls = [s for s in traces.spans if s.span_type == "TOOL"]
        for call in tool_calls:
            if "rm " in str(call.inputs) or "delete" in str(call.inputs):
                return {"score": 0.0, "rationale": "Agent attempted destructive action"}
        return {"score": 1.0, "rationale": "No policy violations"}
```

**Blueprint-specific evaluation examples:**

| Blueprint | Scorer | What It Measures |
| --- | --- | --- |
| BreachLens | `IOCAccuracyScorer` | Did the agent correctly identify indicators of compromise? |
| DataLens | `StatisticalAccuracyScorer` | Did the agent's computed metrics match ground truth? |
| GeneSmith | `BioSafetyScorer` | Did the agent avoid outputting restricted sequences? |
| StarKeep | `PatchSafetyScorer` | Did the agent verify patches in the sandbox before applying? |

**MLflow UI at this stage:**
- Everything from Stages 1-4
- Quality tab populated with scorer results per trace
- Quality trend charts showing agent improvement over time
- Per-trace assessment details with scores and rationale

---

### Stage 6: Prompt Registry (Month 1)

**Effort:** ~40 lines. **Prerequisite:** Stage 5 recommended (to measure prompt quality).

Cognition manages system prompts in three unversioned ways: per-session config, `AGENTS.md` files, and `SKILL.md` files. MLflow's Prompt Registry adds version history, A/B testing, and lineage tracking.

**Where:** `server/app/agent/cognition_agent.py` lines 106-112, where the system prompt is assembled.

```python
# Instead of loading a static string:
prompt = settings.agent_system_prompt or DEFAULT_SYSTEM_PROMPT

# Load a versioned prompt from MLflow:
prompt_template = mlflow.genai.load_prompt("cognition-coding-agent", version="production")
prompt = prompt_template.format(workspace=project_path)
```

**What this enables:**
- Track how prompts evolve over time
- Compare agent quality across prompt variants (A/B testing)
- Link prompt versions to evaluation scores -- which prompt version produces the best agent behavior?
- Roll back to a previous prompt version if quality degrades

**MLflow UI at this stage:**
- Prompt Registry view with version history and diffs
- Lineage tracking linking prompts to traces and evaluation scores

---

### Stage 7: Human Feedback Loop (Month 2)

**Effort:** ~50 lines. **Prerequisite:** Stages 1-2 complete (traces must exist).

MLflow supports attaching human feedback (assessments) to traces. Combined with Cognition, this closes the improvement loop.

**The workflow:**

1. User interacts with the agent via the Cognition API
2. The agent's reasoning chain is captured as an MLflow trace (via auto-instrumentation)
3. User rates the agent's output (thumbs up/down, quality score, correction)
4. Feedback is attached to the trace via the MLflow API:
   ```python
   mlflow.log_feedback(
       trace_id=trace_id,
       name="user_satisfaction",
       value=0.8,
       source_type="HUMAN",
       rationale="Agent found the bug but took too many steps"
   )
   ```
5. Feedback-annotated traces become evaluation datasets
6. Evaluation results inform prompt improvements
7. Improved prompts are deployed via the Prompt Registry
8. The cycle repeats

**MLflow UI at this stage:**
- Feedback annotations visible on individual traces
- Assessment history with user, timestamp, and revision tracking
- Feedback-based filtering to find low-rated sessions for investigation

---

### Stage 8: AI Gateway (Optional)

**Effort:** ~30 lines. **Prerequisite:** None.

MLflow's AI Gateway provides a unified proxy to multiple LLM providers with traffic splitting, rate limiting, and A/B testing. This could replace or complement Cognition's existing provider registry and fallback chain (`server/app/llm/registry.py`, `provider_fallback.py`).

**Where:** `server/app/llm/registry.py`, as an additional provider factory.

```python
# Register MLflow AI Gateway as a provider
def create_gateway_model(settings, config):
    from langchain_community.chat_models import ChatMLflowAIGateway
    return ChatMLflowAIGateway(
        route="cognition-agent",
        gateway_uri=settings.mlflow_gateway_uri,
    )

register_provider("mlflow_gateway", create_gateway_model)
```

**What this adds:**
- Centralized rate limiting and cost controls across all Cognition instances
- A/B testing of models at the provider level (e.g., 80% Claude, 20% GPT-4o)
- Request/response logging at the gateway layer

This is lower priority than Stages 1-7 since Cognition's existing fallback chain is functional.

---

## Deployment Topology

### Development (Single Machine)

```
┌─────────────────────────────────┐
│         Developer Machine        │
│                                  │
│  Cognition Server (:8000)        │
│  MLflow Tracking Server (:5000)  │
│  SQLite (both systems)           │
└─────────────────────────────────┘
```

Both servers run locally. MLflow stores traces in a local SQLite database. Start with:

```bash
# Terminal 1: MLflow
mlflow server --port 5000

# Terminal 2: Cognition (with MLflow enabled)
COGNITION_MLFLOW_ENABLED=true cognition-server
```

### Production (Docker Compose)

Add MLflow to the existing Cognition Docker Compose stack:

```yaml
# docker-compose.yml -- addition to existing stack

services:
  mlflow:
    image: ghcr.io/mlflow/mlflow:latest
    ports:
      - "5000:5000"
    volumes:
      - mlflow-data:/mlflow
    command: >
      mlflow server
      --host 0.0.0.0
      --port 5000
      --backend-store-uri sqlite:///mlflow/mlflow.db
      --default-artifact-root /mlflow/artifacts
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  mlflow-data:
```

This sits alongside the existing Jaeger, Prometheus, and Grafana services. Cognition sends operational metrics to Prometheus/Grafana and GenAI traces to MLflow.

### Production (Kubernetes)

For enterprise deployments, MLflow Tracking Server runs as a separate service backed by PostgreSQL and S3-compatible object storage for artifacts. Cognition pods connect via the cluster-internal service URL.

---

## Summary: MLflow UI Capabilities by Stage

| Stage | MLflow UI Capability |
| --- | --- |
| **1. Auto-instrumentation** | Experiment overview (Usage, Tool Calls tabs). Trace list with search/filter. Full span trees per trace. Automatic token tracking. |
| **2. Agent middleware** | Cognition-specific metadata on spans. Session ID / workspace filtering. |
| **3. Configuration** | User-configurable via YAML / env vars. No UI change. |
| **4. Session tracking** | Traces grouped by session. Run comparison across sessions. Tag-based filtering by model, provider, workspace. |
| **5. Evaluation** | Quality tab populated with scorer results. Quality trend charts. Per-trace assessment details. |
| **6. Prompt Registry** | Prompt version history and diffs. Lineage from prompt to trace to score. |
| **7. Human feedback** | Feedback annotations on traces. Assessment history. Feedback-based filtering. |
| **8. AI Gateway** | Gateway-level request/response logs in traces. |

---

## What Each System Provides

| Capability | Cognition | MLflow GenAI | Together |
| --- | --- | --- | --- |
| Agent execution | Yes | No | Cognition runs the agent |
| Sandboxed tools | Yes | No | Cognition isolates execution |
| Session management | Yes | No | Cognition manages sessions |
| Real-time streaming | Yes | No | Cognition streams via SSE |
| LLM/Tool tracing | Basic (OTel to Jaeger) | Best-in-class (native deepagents integration) | MLflow provides deep GenAI traces |
| Evaluation | None | Core feature (scorers, datasets) | MLflow fills Cognition's biggest gap |
| Prompt versioning | None | Core feature (registry, lineage) | MLflow manages prompt lifecycle |
| Human feedback | None | Core feature (assessments) | MLflow captures and tracks feedback |
| LLM provider proxy | Fallback chain | AI Gateway (A/B, rate limits) | Either or both |
| Operational metrics | Prometheus + Grafana | Not primary focus | Cognition handles ops monitoring |
| Experiment comparison | None | Core feature (runs, metrics) | MLflow enables session comparison |

Neither system replaces the other. Cognition executes; MLflow observes and improves. The native deepagents integration means they connect with minimal friction.