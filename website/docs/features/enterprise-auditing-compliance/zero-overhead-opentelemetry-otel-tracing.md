# Bounded Asynchronous OpenTelemetry Tracing

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The proxy integrates with OpenTelemetry (OTel) to propagate W3C trace context and export traces asynchronously. It uses a bounded batch processor to minimize the impact of telemetry I/O on the primary request path.

## How It Works
The proxy delegates trace export to the standard OTel Python SDK.

1. **Context Extraction:** The proxy reads incoming W3C `traceparent` headers to join existing distributed traces.
2. **Span Creation:** The application creates spans and adds attributes during the request lifecycle.
3. **Batching:** Completed spans are pushed into the OTel SDK's bounded `BatchSpanProcessor`.
4. **Asynchronous Export:** A background worker flushes batches to the configured OTLP endpoint over the network.

## Performance Profile
- **Overhead:** Using a background batch processor removes network latency from the proxy's critical path. However, span creation, attribute serialization, and memory allocation still consume CPU and RAM. It is not "zero overhead."

## Configuration Flags
The proxy uses standard OpenTelemetry environment variables (e.g., `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_TRACES_SAMPLER`).

## Implementation Details & Edge Cases
* **Queue Limits:** If your OTLP collector goes down or becomes slow, the proxy's internal span queue will fill up. Once full, the SDK will drop new spans to protect proxy memory and latency.
* **Data Privacy:** Spans should never contain protected PII or raw prompt text. They are meant for system observability, not data recording.

## FAQ

**Q: Will tracing slow down my proxy requests?**
A: Generating spans has a small CPU cost. However, because the actual network export is handled asynchronously by the `BatchSpanProcessor`, network delays to the OTLP collector will not block proxy requests.

**Q: What happens if the proxy crashes before flushing the batch?**
A: The OTel SDK attempts a graceful shutdown flush, but in an abrupt termination (e.g., `SIGKILL` or OOM), any spans waiting in the memory queue will be lost.

## Practical Effect
This integration allows operators to observe proxy latency, errors, and security decisions in standard APM tools without tightly coupling proxy latency to telemetry collector performance.

## Related Tests
Tests: [`tests/test_tracing.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/tests/test_tracing.py).
