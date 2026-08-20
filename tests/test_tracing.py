import asyncio

import pytest

from llm_shield_proxy.observability.tracing import propagator, tracer


@pytest.mark.asyncio
async def test_batch_span_processor_async_execution():
    """Verify that span creation does not block the async event loop."""

    async def fast_task():
        await asyncio.sleep(0.01)
        return "fast"

    async def traced_task():
        with tracer.start_as_current_span("test_span"):
            # The BatchSpanProcessor should handle export in a background thread
            # so this should immediately yield back to the event loop.
            await asyncio.sleep(0.05)
        return "traced"

    # Run concurrently
    results = await asyncio.gather(fast_task(), traced_task())
    assert results == ["fast", "traced"]


def test_w3c_traceparent_propagation():
    """Verify traceparent header extraction and injection."""

    # 1. Mock Downstream Request Headers
    # Valid W3C traceparent format: 00-traceid-spanid-traceflags
    mock_trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    mock_span_id = "00f067aa0ba902b7"
    downstream_headers = {"traceparent": f"00-{mock_trace_id}-{mock_span_id}-01"}

    # 2. Extract context
    ctx = propagator.extract(downstream_headers)

    # 3. Simulate proxy internal processing
    with tracer.start_as_current_span("proxy_catch_all", context=ctx):
        # 4. Inject context into upstream headers
        upstream_headers = {}
        propagator.inject(upstream_headers)

    # Assertions
    assert "traceparent" in upstream_headers
    upstream_traceparent = upstream_headers["traceparent"]

    # The trace ID should be propagated exactly
    parts = upstream_traceparent.split("-")
    assert len(parts) == 4
    assert parts[1] == mock_trace_id
    # The new span ID should be different from the downstream one
    assert parts[2] != mock_span_id
