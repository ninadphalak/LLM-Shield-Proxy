# Decision Trace Exporter

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The **Decision Trace Exporter** allows the proxy to package selected security and governance decision metadata into OpenTelemetry (OTel) spans. This bridges proxy security decisions with standard observability infrastructure.

## How It Works
The exporter translates internal proxy decisions into standard OTel `gen_ai.client.operation.tool_call` spans.

1. **Invocation:** Application code constructs a `DecisionTraceExporter` and invokes `record_decision(...)`.
2. **Span Emission:** The exporter maps the decision arguments into an OTel span and attaches the relevant metadata as span attributes.
3. **Dispatch:** The span enters the OTel pipeline. GRC transports can optionally receive a single-event OSCAL payload in a background task.

```mermaid
flowchart LR
    A[Incoming Request] --> B(Create OTel Span)
    B --> C(Security Engine)
    C -->|Redact SSN| D[Add Span Event]
    C -->|Block Tool| D
    D --> E[Export to OTel Collector]
```

## Performance Profile
- **Overhead:** Depends entirely on your OpenTelemetry SDK configuration (e.g., batch processors vs. synchronous exporters).

## Configuration Flags
The exporter integrates with standard OpenTelemetry environment variables (`TELEMETRY_ENABLED`, `TELEMETRY_ENDPOINT_URL`). There is no separate `ENABLE_DECISION_TRACES` flag; it must be wired by application code.

## Implementation Details & Edge Cases
* **Data Minimization:** Traces are designed to record *decisions* (e.g., "Tool blocked"), not the sensitive values that triggered them.
* **Trace Context:** The proxy parses incoming W3C `traceparent` headers to join existing distributed traces. Whether the upstream LLM provider propagates this context back to you depends on the provider.

## FAQ

**Q: Can I view these traces in Datadog, New Relic, or Honeycomb?**
A: Yes, as long as your collector supports OTLP. You must test the pipeline to ensure the specific span attributes map correctly in your observability tool.

**Q: Does this replace signed audit logs?**
A: No. OTel traces are typically sampled and have short retention periods. Formal compliance requires durable, immutable audit logs (like the hash-chained JSONL logs).

## Practical Effect
This feature allows operators to monitor security decisions in real-time through standard APM and observability dashboards, separate from formal audit evidence collection.

## Related Tests
Tests: [`tests/test_audit_remediation.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_audit_remediation.py).
