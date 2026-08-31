# Bounded Asynchronous OpenTelemetry Tracing

[Back to Features Catalog](/docs/features-overview)

## Purpose

The tracing integration propagates supported W3C trace context and uses OpenTelemetry's
`BatchSpanProcessor` to move exporter network I/O away from the ASGI request task. This reduces
request-path exporter work; it does not create zero-overhead or lossless tracing.

## Execution model

1. Supported request paths extract W3C `traceparent` context.
2. The application creates and updates spans on the request path.
3. Completed spans enter the SDK's bounded batch processor.
4. A background worker exports batches to the configured OTLP endpoint.

Span creation, attribute processing, context propagation, queue operations, serialization, memory,
and synchronization still consume resources. When the exporter or collector is slow, the queue can
fill and spans can be delayed or dropped according to SDK configuration.

## Configuration and validation

Configure sampling, queue size, batch size, exporter timeout, endpoint, and credentials using the
documented OpenTelemetry settings. Include the exact configuration in load tests and confirm:

- request latency with tracing disabled and enabled;
- queue and drop behavior when the collector is unavailable;
- shutdown flushing behavior;
- memory under the intended span rate; and
- that attributes, events, exceptions, and resource metadata contain no protected values.

Async or background export does not mean the instrumentation is outside the Python process or
unaffected by CPU and memory contention.

## Related implementation and tests

- `llm_shield_proxy/observability/tracing.py`
- `tests/test_tracing.py`
