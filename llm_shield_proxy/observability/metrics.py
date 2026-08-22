"""Prometheus Metrics Instrumentation for LLM-Shield-Proxy."""

from prometheus_client import Counter, Gauge, Histogram

llm_shield_requests_total = Counter(
    "llm_shield_requests_total",
    "Total number of HTTP proxy requests handled",
    ["status_code"],
)

llm_shield_pii_redacted_total = Counter(
    "llm_shield_pii_redacted_total",
    "Total count of PII entities redacted",
    ["entity_type"],
)

llm_shield_sse_active_streams = Gauge(
    "llm_shield_sse_active_streams",
    "Number of active SSE streaming responses currently being rehydrated",
)

llm_shield_latency_seconds_bucket = Histogram(
    "llm_shield_latency_seconds",
    "End-to-end request processing latency in seconds",
)

try:
    from prometheus_client import REGISTRY
    if "llm_shield_tokens_total" in REGISTRY._names_to_collectors:
        llm_shield_tokens_total = REGISTRY._names_to_collectors["llm_shield_tokens_total"]
    else:
        llm_shield_tokens_total = Counter(
            "llm_shield_tokens_total",
            "Total number of tokens consumed by the virtual key",
            ["virtual_key_id", "model", "type"],
        )
except ValueError:
    pass

llm_shield_vault_refresh_errors_total = Counter(
    "llm_shield_vault_refresh_errors_total",
    "Total number of background Vault secret refresh errors",
)
