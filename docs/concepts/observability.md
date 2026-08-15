# Observability

Cognition provides a three-pillar observability stack — distributed traces, time-series metrics, and experiment tracking — all independently toggleable and all with graceful degradation when the underlying packages are not installed.

!!! note "v0.13 implementation note: curated Agent tracing"

    The v0.13 branch follows
    [ADR-0002](../architecture/decisions/0002-curated-opentelemetry-agent-tracing.md)
    and the
    [curated tracing proposal](../proposals/curated-opentelemetry-tracing.md):
    one active `cognition.agent.run` application span per durable run,
    canonical OTLP delivery, automatic framework instrumentation, and
    provider-authoritative Usage Events. LangGraph, model, tool, and subagent
    spans inherit that active context and remain in the same semantic trace. A
    split GenAI trace is a context-propagation regression. Local observability
    smoke validation remains a release gate.

---

## Three Pillars

| Pillar | Technology | Purpose | Toggle |
|---|---|---|---|
| Distributed traces | OpenTelemetry → OTLP → Collector | Request and LLM call tracing | `COGNITION_TRACING_ENABLED` |
| Time-series metrics | Prometheus | Counters and histograms | `COGNITION_METRICS_ENABLED` |
| Experiment tracking | MLflow via Collector | LLM evaluation and run history | Collector configuration |

Tracing and metrics are initialised in the FastAPI lifespan in
`server/app/main.py`. MLflow is a downstream OTLP destination configured in the
operator-owned Collector, not by Cognition application startup.

---

## OpenTelemetry Traces

Implemented in `server/app/observability/__init__.py:setup_tracing()`.

### What Gets Traced

- **HTTP requests** — every inbound request with method, matched route template, status class, and duration
- **LLM calls** — latency and sampled raw framework trace details when tracing is enabled
- **Tool executions** — duration and success/failure, with tool names kept out of Prometheus labels

### Configuration

| Variable | Default | Description |
|---|---|---|
| `COGNITION_TRACING_ENABLED` | `false` | Enable/disable tracing. `COGNITION_OTEL_ENABLED` remains a compatibility alias. |
| `COGNITION_OTLP_ENDPOINT` | `null` | Canonical OTLP collector URL (e.g. `http://localhost:4317`). `COGNITION_OTEL_ENDPOINT` remains a compatibility alias. |
| `COGNITION_OTLP_MAX_EXPORT_BYTES` | `3670016` | Maximum encoded OTLP trace export request size. `COGNITION_OTEL_MAX_EXPORT_BYTES` remains a compatibility alias. |
| `COGNITION_OTLP_QUEUE_SIZE` | `2048` | Maximum queued trace spans before export |
| `COGNITION_OTLP_EXPORT_TIMEOUT_MS` | `30000` | Per-attempt trace export deadline |
| `COGNITION_OTLP_METRIC_EXPORT_INTERVAL_MS` | `60000` | OTLP metric export interval |
| `COGNITION_TRACE_SAMPLE_RATIO` | `0.10` | Parent-based root trace sample ratio for normal runs |
| `COGNITION_TRACE_DETAIL` | `standard` | `standard` omits duplicate/standalone hook instrumentation while preserving native semantic parents; `debug` keeps additional framework internals |

Cognition emits raw trace content supplied by its root run span and enabled
auto-instrumentation. Builders own downstream trace redaction, access control,
retention, and export policy in their observability pipelines.

Transport is auto-detected: gRPC for `http://host:4317`-style endpoints, HTTP for `/v1/traces` paths. When `COGNITION_OTLP_ENDPOINT` is null, traces are not exported but the instrumentation is still active (useful for local development with a local Jaeger or similar).

### Manual Instrumentation

```python
from server.app.observability import traced, span, get_tracer

# Decorator form
@traced("my_operation")
async def do_work():
    ...

# Context manager form
async def process():
    async with span("processing_step", {"input_size": len(data)}):
        result = await heavy_computation(data)
```

### Docker Compose Stack

The `docker-compose.yml` ships a full OTel pipeline:

```
Cognition → OTel Collector (port 4317) → Jaeger (traces)
                                        → Loki (logs via Promtail)
```

See the [Deployment guide](../guides/deployment.md) for the complete stack.

---

## Prometheus Metrics

Implemented in `server/app/observability/__init__.py`. All metrics are defined at module level and imported by the middleware and agent layers.

### Defined Metrics

| Metric | Type | Labels | Description |
|---|---|---|---|
| `cognition_requests_total` | Counter | `method`, route-template `endpoint`, status class `status` | Total HTTP requests |
| `cognition_request_duration_seconds` | Histogram | `method`, route-template `endpoint` | HTTP request latency |
| `cognition_llm_call_duration_seconds` | Histogram | — | LLM API call latency |
| `cognition_tool_calls_total` | Counter | `status` | Tool invocations (`success`/`error`) |
| `cognition_active_sessions` | Gauge | — | Open non-terminal sessions |
| `cognition_a2a_requests_total` | Counter | `operation`, `outcome` | A2A operation outcomes |
| `cognition_runtime_task_transitions_total` | Counter | `transport`, `status` | Durable task transitions |
| `cognition_runtime_active_tasks` | Gauge | `transport` | Active in-process executions |
| `cognition_a2a_active_subscribers` | Gauge | — | Active A2A subscribers |
| `cognition_runtime_time_to_first_output_seconds` | Histogram | `transport` | Time to first output |
| `cognition_runtime_task_duration_seconds` | Histogram | `transport`, `outcome` | Execution duration |
| `cognition_a2a_stream_chunk_bytes` | Histogram | — | Coalesced artifact-update size |
| `cognition_a2a_stream_flush_duration_seconds` | Histogram | — | Persist-and-enqueue latency |
| `cognition_a2a_subscriptions_total` | Counter | `outcome` | Subscription lifecycle outcomes |
| `cognition_a2a_idempotency_total` | Counter | `outcome` | Retry reuse and conflicts |
| `cognition_a2a_limit_rejections_total` | Counter | `direction`, `limit` | Resource-limit rejections |
| `cognition_runtime_task_cleanup_total` | Counter | `transport`, `outcome` | Retention cleanup results |
| `cognition_runtime_task_cleanup_duration_seconds` | Histogram | `transport` | Cleanup pass duration |
| `cognition_scope_access_denied_total` | Counter | `resource_type`, `operation` | Explicit scope-policy rejections without exposing scope values |
| `cognition_runtime_manifest_resolutions_total` | Counter | `outcome` | Run-manifest pinning outcomes |
| `cognition_runtime_manifest_resolution_seconds` | Histogram | `outcome` | Run-manifest pinning latency |
| `cognition_runtime_cache_lookups_total` | Counter | `cache`, `outcome`, `reason` | Agent graph cache hit/miss/stale behavior |
| `cognition_runtime_cache_size` | Gauge | `cache` | In-process bounded cache occupancy |
| `cognition_runtime_cache_evictions_total` | Counter | `cache`, `reason` | Cache TTL/capacity/config/manual eviction pressure |
| `cognition_storage_operations_total` | Counter | `backend`, `operation`, `result` | Scoped storage access outcomes |
| `cognition_storage_operation_duration_seconds` | Histogram | `backend`, `operation` | Scoped storage operation latency |
| `cognition_sandbox_lifecycle_total` | Counter | `backend`, `stage`, `outcome` | Sandbox create/materialization/cleanup outcomes |
| `cognition_sandbox_lifecycle_duration_seconds` | Histogram | `backend`, `stage` | Sandbox lifecycle latency |
| `cognition_strict_execution_rejections_total` | Counter | `reason` | Host/local/dynamic-tool/callback rejection evidence |
| `cognition_telemetry_export_batches_total` | Counter | `signal`, `transport`, `outcome` | Trace export health |
| `cognition_telemetry_dropped_items_total` | Counter | `signal`, `reason` | Dropped telemetry items |
| `cognition_telemetry_batch_splits_total` | Counter | `signal`, `reason` | Byte-limit split activity |
| `cognition_telemetry_last_success_unixtime` | Gauge | `signal`, `transport` | Last successful export timestamp |

A2A adapter metrics intentionally exclude agent names, task/message IDs, and raw scope
values. Runtime metrics use the bounded `transport` label (currently `a2a`) so
the same metric families can cover native API and future protocol adapters.
Recommended alerts cover sustained limit rejections, idempotency conflicts,
cleanup errors, increasing time-to-first-event, and nonzero active tasks without
corresponding terminal transitions.

When `prometheus_client` is not installed, all metrics fall back to `DummyMetric` — a no-op object that accepts any call without error.

### Scrape Configuration

Metrics are served on a separate port from the API:

```env
COGNITION_METRICS_ENABLED=true
COGNITION_METRICS_PORT=9090
```

Prometheus `prometheus.yml` scrape target:

```yaml
scrape_configs:
  - job_name: cognition
    static_configs:
      - targets: ["cognition:9090"]
```

### Where Metrics Are Recorded

- `REQUEST_COUNT` and `REQUEST_DURATION` — `server/app/api/middleware.py:ObservabilityMiddleware` (every request, labelled by matched route template and status class rather than concrete resource IDs)
- `LLM_CALL_DURATION` — `server/app/agent/middleware.py:CognitionObservabilityMiddleware` (every LLM invocation)
- `TOOL_CALL_COUNT` — `server/app/agent/middleware.py:CognitionObservabilityMiddleware` (every tool invocation, labelled `success` or `error`; tool names stay out of Prometheus labels)
- `SESSION_COUNT` — updated by `server/app/api/routes/sessions.py` on session create and delete
- Runtime isolation counters — recorded by session/message/task runtime gates,
  manifest resolution, graph cache lookup/invalidation, sandbox creation, and
  strict execution rejection paths. These labels are bounded enums only; IDs,
  names, scopes, URLs, paths, and digests stay out of metrics.

### Decorator Form

```python
from server.app.observability import LLM_CALL_DURATION, timed

@timed(LLM_CALL_DURATION)
async def call_model(messages):
    ...
```

Prometheus labels are intentionally narrow. Do not add tenant, scope, Agent,
tool, model, session, run, trace, digest, callback URL, exception text, or raw
path values as labels; use redacted logs and raw sampled traces for per-run
diagnosis.

---

## MLflow Experiment Tracking

### How It Works

MLflow receives traces via the OTel Collector — there is no direct MLflow SDK call in the hot path. The flow is:

```
Cognition (OTel SDK) → OTel Collector → MLflow Tracking Server
```

Cognition does not create MLflow experiments or configure the MLflow Python
client at startup. The Collector owns the MLflow destination endpoint and the
`x-mlflow-experiment-id` header required by MLflow's OTLP ingest endpoint. The
local Compose stack defaults that experiment id to `0`; production operators
should set the approved experiment id in Collector configuration.

### Configuration

| Collector setting | Default | Description |
|---|---|---|
| `otlphttp/mlflow.endpoint` | `http://mlflow:5000` in Compose | MLflow server URL |
| `x-mlflow-experiment-id` | `${env:MLFLOW_EXPERIMENT_ID}` in Compose | MLflow experiment id for OTLP traces |
| `compression` | `gzip` in Compose | OTLP/HTTP compression for MLflow ingest |

### What Gets Recorded

With the local MLflow/Collector stack enabled, Cognition exports raw
OpenTelemetry traces to MLflow for operator debugging and evaluation workflows.
The Collector owns trace ingestion and metrics fan-out. Cognition does not
enable direct MLflow or LangSmith autolog destination modes; LangSmith's
OTel-only bridge is used only as an in-process instrumentation adapter that
emits through Cognition's global OpenTelemetry provider.

### Offline Evaluation Pipeline

The evaluation pipeline runs independently of the live server. It replays sessions from the `StorageBackend`, scores them with built-in scorers, and logs results as MLflow runs.

Built-in scorers:
- **Faithfulness** — LLM-as-judge scoring whether the response is grounded in sources
- **Relevance** — Whether the response addresses the question
- **Tool efficiency** — Whether the agent used the minimum necessary tool calls

See `MLFLOW-INTEROPERABILITY.md` in the repository root for the full evaluation workflow.

---

## Structured Logging

`server/app/observability/__init__.py:setup_logging()` configures structlog.

Set `COGNITION_LOG_FORMAT=console` for a human-readable local renderer. The
production default is `COGNITION_LOG_FORMAT=json` for ingestion by Loki,
Datadog, CloudWatch, or another structured log aggregator.

HTTP middleware binds a small correlation envelope to each request:
`request_id`, sorted scope key names, and active trace/span ids when tracing is
available. The request id is returned as `X-Request-ID`; caller-provided ids are
accepted only when they match a bounded safe character set.

Durable run/event projection temporarily binds safe runtime context around run
begin, event append, transition, and checkpoint-projection operations:
`session_id`, `run_id`, `thread_id`, optional `task_id`, Agent revision,
manifest digest, and sorted scope key names.

A central redaction processor removes raw scope dictionaries, database
`scope_key` values, authorization headers, cookies, tokens, passwords, API keys,
and credential-like fields before rendering logs.

```env
COGNITION_LOG_LEVEL=info    # debug | info | warning | error
COGNITION_LOG_FORMAT=json   # json | console
```

Log output from the Docker Compose stack is collected by Promtail and forwarded to Loki, then queryable in Grafana.

---

## Grafana Dashboards

The `docker/grafana/dashboards/` directory ships pre-built Grafana dashboard JSON for:

- **Cognition Overview** — Request rate, latency, error rate, active sessions
- **LLM Performance** — Aggregate model-call latency and runtime outcomes
- **Tool Execution** — Aggregate tool success/error ratio
- **Session Activity** — Session creation rate, message throughput

Dashboards are provisioned automatically when starting the Docker Compose stack.

---

## Graceful Degradation

All three observability subsystems are wrapped in conditional imports:

```python
try:
    import prometheus_client
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    # All metric objects become DummyMetric()
```

The server starts and runs normally regardless of whether `prometheus_client`, `opentelemetry-sdk`, or `mlflow` are installed. Observability is additive, not required.
