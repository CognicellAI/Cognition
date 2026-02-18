# Audit Trails ("The Trace")

> **Trust is good. Proof is better.**

In regulated industries (Security, Healthcare, Finance), it is not enough for an AI to be "right." It must be **accountable**. If an Agent deletes a file or approves a transaction, you need a mathematically provable record of *why* it did that.

Cognition treats Observability not just as a debugging tool, but as a **Compliance Primitive**.

## The Chain of Events

Cognition generates a continuous, immutable stream of events called the **Trace**. This Trace captures the full cognitive lifecycle of an action.

### Anatomy of a Trace

A single "Action" by an Agent results in a structured OTLP Trace containing:

1.  **The Trigger:** (e.g., User Prompt: "Scan the logs")
2.  **The Reasoning:** (The LLM's internal monologue/Chain of Thought)
    *   *"I see a large log file. I should not read it all at once. I will sample the first 100 lines."*
3.  **The Tool Call:** (The exact command executed)
    *   `head -n 100 /mnt/data/access.log`
4.  **The Output:** (The raw result returned by the Kernel)
5.  **The Conclusion:** (The final summary presented to the user)

### Technology: OpenTelemetry (OTLP)

Cognition is built on native OpenTelemetry. This means:

*   **Vendor Agnostic:** We do not lock you into a proprietary dashboard.
*   **Export Anywhere:** Pipe traces to Jaeger, Splunk, Datadog, Honeycomb, or a custom SQL warehouse.
*   **Standardized:** Uses the W3C Trace Context standard for distributed tracing across microservices.

## Compliance Use Cases

### 1. Legal Discovery
*   **Scenario:** An Agent analyzes thousands of contracts during a merger.
*   **Requirement:** Lawyers need to verify the Agent didn't hallucinate a clause.
*   **Solution:** The Trace links every claim in the final report back to the specific file fragment (`contract.pdf:page 42`) that generated it.

### 2. Incident Response (SOC)
*   **Scenario:** An Agent remediates a malware infection by deleting files.
*   **Requirement:** You need to prove the Agent didn't delete critical system files by mistake.
*   **Solution:** The Trace logs the exact `rm` command and the SHA256 hash of the target file before deletion.

### 3. Financial Advisory
*   **Scenario:** An Agent recommends a stock trade.
*   **Requirement:** SEC regulations require logging the "advice rationale."
*   **Solution:** The Trace captures the retrieved market data and the logic step used to formulate the recommendation.

## Integration

Integrating the Audit Trail into your platform is simple configuration.

```yaml
# .cognition/config.yaml
observability:
  otel_enabled: true
  otel_endpoint: "https://audit-vault.internal:4317"
```

## Dashboarding

While you can build your own, Cognition provides pre-built Grafana dashboards to visualize:
*   **Agent Decision Trees:** Visual flowcharts of the agent's logic.
*   **Tool Usage Audits:** Tables showing every shell command executed across the fleet.
*   **Cost/Token Audits:** Financial tracking of AI usage.
