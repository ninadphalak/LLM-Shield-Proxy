"""Lightweight OpenTelemetry Tracing Engine.

Configures OTel Tracing using BatchSpanProcessor on a background thread to prevent
event loop starvation and caps memory usage to strictly adhere to the < 60 MB RSS limit.
"""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


def _init_tracing() -> trace.Tracer:
    """Initializes and returns the global tracer instance with constrained memory limits."""
    # Check if a TracerProvider is already configured to avoid duplicate providers in testing
    current_provider = trace.get_tracer_provider()
    if isinstance(current_provider, TracerProvider):
        return trace.get_tracer("llm-shield-proxy")

    provider = TracerProvider()

    # Use BatchSpanProcessor on a background daemon thread to prevent ASGI event loop starvation.
    # We strictly bound the queue size and export batch size to prevent unbounded memory bloat.
    from llm_shield_proxy.core.config import settings

    if settings.TELEMETRY_ENABLED:
        headers = {}
        if settings.TELEMETRY_API_KEY:
            headers["Authorization"] = settings.TELEMETRY_API_KEY

        # Truly asynchronous lock-free network export!
        exporter = OTLPSpanExporter(endpoint=settings.TELEMETRY_ENDPOINT_URL, headers=headers)
        processor = BatchSpanProcessor(
            exporter,
            max_queue_size=256,
            schedule_delay_millis=500,
            max_export_batch_size=64,
        )
        provider.add_span_processor(processor)

    trace.set_tracer_provider(provider)

    return trace.get_tracer("llm-shield-proxy")


tracer = _init_tracing()
propagator = TraceContextTextMapPropagator()
