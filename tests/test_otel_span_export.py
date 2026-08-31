"""OpenTelemetry span export, asserted against a real exporter.

``tests/conftest.py`` disables OTLP export before importing the app, because the
tracer provider is built at import time and an unreachable collector would
otherwise spawn a retrying background thread for the whole session. That is the
right call for the rest of the suite, but it also meant nothing ever verified
that a span leaves the process at all -- only that ``start_as_current_span``
does not raise.

These tests attach an ``InMemorySpanExporter`` behind a real
``BatchSpanProcessor`` -- the same processor class the production wiring uses --
to the live tracer provider, drive real traffic through the ASGI app, then
``force_flush()`` and inspect what the exporter actually received. The queue,
the background export thread and the ``SpanExporter`` interface are all
exercised; only the OTLP-over-HTTP transport is substituted.
"""

from __future__ import annotations

import json
from typing import Iterator, List

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from llm_shield_proxy.api.main import app
from llm_shield_proxy.core.config import settings

client = TestClient(app)


def _installed_processors(provider) -> list:
    active = getattr(provider, "_active_span_processor", None)
    return list(getattr(active, "_span_processors", ()))


# Snapshotted at import, before any fixture in this module attaches its own
# processor. BatchSpanProcessor.shutdown() stops a processor but does not detach
# it from the provider, so a snapshot taken later would just be counting this
# file's own leftovers.
_PROCESSORS_AT_REST = _installed_processors(trace.get_tracer_provider())

UPSTREAM = "https://api.openai.com/v1/chat/completions"

# A PII-bearing prompt, so the "no PII on the telemetry path" invariant is
# checked against spans produced by a request that really did carry PII.
PII_PROMPT = "My name is John Doe and my phone number is 555-0199."


class _Collector:
    """Owns the in-memory exporter and the processor attached to the provider."""

    def __init__(self, exporter: InMemorySpanExporter, processor: BatchSpanProcessor):
        self._exporter = exporter
        self._processor = processor

    def flush(self) -> List[ReadableSpan]:
        """Force the batch processor to drain, then return everything exported."""
        assert self._processor.force_flush(timeout_millis=5000), "BatchSpanProcessor failed to flush"
        return list(self._exporter.get_finished_spans())

    def names(self) -> List[str]:
        return [span.name for span in self.flush()]


@pytest.fixture
def spans() -> Iterator[_Collector]:
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider), (
        "the SDK tracer provider is not installed; llm_shield_proxy.observability.tracing "
        "did not initialise, so nothing here would be measuring the real code path"
    )

    exporter = InMemorySpanExporter()
    processor = BatchSpanProcessor(
        exporter,
        max_queue_size=256,
        schedule_delay_millis=50,
        max_export_batch_size=64,
    )
    provider.add_span_processor(processor)
    try:
        yield _Collector(exporter, processor)
    finally:
        processor.shutdown()
        exporter.clear()


def _post(headers: dict | None = None, prompt: str = PII_PROMPT):
    return client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock-key", **(headers or {})},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]},
    )


def _mock_upstream(httpx_mock, content: str = "Acknowledged.") -> None:
    httpx_mock.add_response(
        method="POST",
        url=UPSTREAM,
        json={"id": "chatcmpl-otel", "choices": [{"message": {"role": "assistant", "content": content}}]},
    )


def test_proxied_request_exports_spans_through_a_real_exporter(spans, httpx_mock):
    """A request produces spans that a SpanExporter actually receives."""
    _mock_upstream(httpx_mock)

    assert _post().status_code == 200

    exported = spans.flush()
    assert exported, "no spans reached the exporter; export is not happening"

    names = [span.name for span in exported]
    assert "proxy_catch_all" in names, f"request span was never exported (got {names})"
    # The detection cascade instruments its own tier; this proves nested spans
    # inside the hot path are exported too, not just the outermost one.
    assert "regex_tier" in names, f"detection-tier span was never exported (got {names})"


def test_exported_spans_share_one_trace_and_a_real_parent_child_link(spans, httpx_mock):
    """The tier span is a genuine child of the request span, not a sibling."""
    _mock_upstream(httpx_mock)
    assert _post().status_code == 200

    exported = spans.flush()
    by_name = {span.name: span for span in exported}
    request_span = by_name["proxy_catch_all"]
    tier_span = by_name["regex_tier"]

    assert tier_span.context.trace_id == request_span.context.trace_id
    assert tier_span.parent is not None
    assert tier_span.parent.span_id == request_span.context.span_id
    assert request_span.end_time is not None and request_span.end_time > request_span.start_time


def test_inbound_traceparent_is_adopted_by_the_exported_span(spans, httpx_mock):
    """W3C context from the caller continues into the spans we export.

    ``test_tracing.py`` checks the propagator in isolation; this checks that the
    proxy's own request span really joins the caller's trace, measured from the
    exporter's side rather than from the header dict.
    """
    _mock_upstream(httpx_mock)
    trace_id_hex = "4bf92f3577b34da6a3ce929d0e0e4736"
    parent_span_hex = "00f067aa0ba902b7"

    response = _post({"traceparent": f"00-{trace_id_hex}-{parent_span_hex}-01"})
    assert response.status_code == 200

    request_span = {span.name: span for span in spans.flush()}["proxy_catch_all"]

    assert format(request_span.context.trace_id, "032x") == trace_id_hex
    assert request_span.parent is not None
    assert format(request_span.parent.span_id, "016x") == parent_span_hex
    assert request_span.context.span_id != request_span.parent.span_id


def test_streaming_requests_export_the_buffer_flush_span(spans, httpx_mock, monkeypatch):
    """SSE rehydration is instrumented, and those spans are exported too."""
    monkeypatch.setattr(settings, "ENABLE_SYNTHETIC_SWAPPING", False)
    monkeypatch.setattr(settings, "ENABLE_CANARY_TRIPWIRE", False)
    httpx_mock.add_response(
        method="POST",
        url=UPSTREAM,
        content=(
            b'data: {"choices":[{"delta":{"content":"Hello "}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"[PERSON_1]"}}]}\n\n'
            b"data: [DONE]\n\n"
        ),
        headers={"content-type": "text/event-stream"},
    )

    with client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock-key"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": PII_PROMPT}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert "John Doe" in body, "stream did not rehydrate; the wrong path was measured"
    assert "buffer_flush" in spans.names()


def test_exported_spans_carry_no_pii(spans, httpx_mock):
    """The no-PII-in-telemetry invariant, checked on exported span payloads.

    Spans travel to a third-party collector, so a leak here escapes the network
    boundary the proxy exists to defend. This asserts over the serialised span
    -- name, attributes, events and status -- not just over attribute values.
    """
    _mock_upstream(httpx_mock, content="Noted, [PERSON_1] at [PHONE_1].")
    assert _post().status_code == 200

    secrets = ("John Doe", "555-0199")
    for span in spans.flush():
        serialised = span.to_json()
        for secret in secrets:
            assert secret not in serialised, f"span {span.name!r} leaked {secret!r}: {serialised}"
        # to_json() should be self-consistent JSON; a malformed payload would
        # mean the assertion above was scanning something other than the span.
        json.loads(serialised)


def test_export_stays_off_when_no_collector_is_configured():
    """Guards the conftest boundary itself.

    If a future change made the app attach an OTLP exporter regardless of
    settings, the whole suite would start emitting spans to localhost:4318. The
    provider must carry no exporting processor of its own.
    """
    assert settings.TELEMETRY_ENABLED is False
    assert not settings.TELEMETRY_ENDPOINT_URL
    assert _PROCESSORS_AT_REST == [], (
        "the tracer provider has an exporting processor installed at rest; "
        f"tests would ship spans off-box: {_PROCESSORS_AT_REST}"
    )
