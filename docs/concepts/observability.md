# Observability

Cognition provides a three-pillar observability stack — distributed traces, time-series metrics, and experiment tracking — all independently toggleable and all with graceful degradation when the underlying packages are not installed.

---

## Three Pillars

| Pillar | Technology | Purpose | Toggle |
|---|---|---|---|
| Distributed traces | OpenTelemetry → OTLP → Collector | Request and LLM call tracing | `COGNITION_OTEL_ENABLED` |
| Time-series metrics | Prometheus | Counters and histograms | `COGNITION_METRICS_PORT` |
| Experiment tracking | MLflow | LLM evaluation and run history | `COGNITION_MLFLOW_ENABLED` |

All three subsystems are initialised in the FastAPI lifespan in `server/app/main.py`. Disabling any one of them requires only an environment variable change — no code changes.

---

## OpenTelemetry Traces

Implemented in `server/app/observability/__init__.py:setup_tracing()`.

### What Gets Traced

- **HTTP requests** — every inbound request with method, path, status code, and duration (via FastAPI auto-instrumentation)
- **LLM calls** — provider, model, input/output token counts, latency (via LangChain auto-instrumentation)
- **Tool executions** — tool name, duration, success/failure

### Configuration

| Variable | Default | Description |
|---|---|---|
| `COGNITION_OTEL_ENABLED` | `true` | Enable/disable tracing |
| `COGNITION_OTEL_ENDPOINT` | `null` | OTLP collector URL (e.g. `http://localhost:4317`) |

Transport is auto-detected: gRPC for `http://host:4317`-style endpoints, HTTP for `/v1/traces` paths. When `COGNITION_OTEL_ENDPOINT` is null, traces are not exported but the instrumentation is still active (useful for local development with a local Jaeger or similar).

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
| `cognition_requests_total` | Counter | `method`, `endpoint`, `status` | Total HTTP requests |
| `cognition_request_duration_seconds` | Histogram | `method`, `endpoint` | HTTP request latency |
| `cognition_llm_call_duration_seconds` | Histogram | `provider`, `model` | LLM API call latency |
| `cognition_tool_calls_total` | Counter | `tool_name`, `status` | Tool invocations (`success`/`error`) |
| `cognition_active_sessions` | Gauge | — | Currently active sessions |

When `prometheus_client` is not installed, all metrics fall back to `DummyMetric` — a no-op object that accepts any call without error.

### Scrape Configuration

Metrics are served on a separate port from the API:

```env
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

- `REQUEST_COUNT` and `REQUEST_DURATION` — `server/app/api/middleware.py:ObservabilityMiddleware` (every request)
- `LLM_CALL_DURATION` — `server/app/agent/middleware.py:CognitionObservabilityMiddleware` (every LLM invocation)
- `TOOL_CALL_COUNT` — `server/app/agent/middleware.py:CognitionObservabilityMiddleware` (every tool invocation, labelled `success` or `error`)
- `SESSION_COUNT` — updated by `server/app/api/routes/sessions.py` on session create and delete

### Decorator Form

```python
from server.app.observability import timed, TOOL_CALL_COUNT

@timed(TOOL_CALL_COUNT, {"tool_name": "my_tool"})
async def my_tool_handler(args):
    ...
```

---

## MLflow Experiment Tracking

Implemented in `server/app/observability/mlflow_config.py`.

### How It Works

MLflow receives traces via the OTel Collector — there is no direct MLflow SDK call in the hot path. The flow is:

```
Cognition (OTel SDK) → OTel Collector → MLflow Tracking Server
```

`setup_mlflow_tracing(settings)` performs two things at startup:
1. Sets the MLflow tracking URI
2. Creates or sets the MLflow experiment (default name: `cognition`)

### Configuration

| Variable | Default | Description |
|---|---|---|
| `COGNITION_MLFLOW_ENABLED` | `false` | Enable MLflow integration |
| `COGNITION_MLFLOW_TRACKING_URI` | `null` | MLflow server URL (e.g. `http://localhost:5000`) |
| `COGNITION_MLFLOW_EXPERIMENT_NAME` | `cognition` | Experiment name |

### What Gets Recorded

Each agent turn becomes an MLflow run with:
- Trace of every LLM call (model, tokens, latency)
- Trace of every tool invocation (name, args, output, duration)
- Nested delegation spans when subagents are involved
- Custom `evaluation` runs from the offline evaluation pipeline

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

In development (`log_level: debug`), logs are rendered in a human-readable console format with coloured level names and formatted timestamps.

In production (`log_level: info` or `warning`), logs are rendered as JSON for ingestion by Loki, Datadog, CloudWatch, or any structured log aggregator.

```env
COGNITION_LOG_LEVEL=info    # debug | info | warning | error
```

Log output from the Docker Compose stack is collected by Promtail and forwarded to Loki, then queryable in Grafana.

---

## Grafana Dashboards

The `docker/grafana/dashboards/` directory ships pre-built Grafana dashboard JSON for:

- **Cognition Overview** — Request rate, latency, error rate, active sessions
- **LLM Performance** — Per-provider call latency, token usage, circuit breaker state
- **Tool Execution** — Tool call rate by name, success/error ratio
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
