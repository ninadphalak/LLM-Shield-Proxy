from prometheus_client import Counter, Gauge, Histogram

llm_shield_requests_total = Counter('llm_shield_requests_total', 'Total proxy requests', ['status_code'])
llm_shield_pii_redacted_total = Counter('llm_shield_pii_redacted_total', 'Total PII entities redacted', ['entity_type'])
llm_shield_sse_active_streams = Gauge('llm_shield_sse_active_streams', 'Active SSE streams')
llm_shield_latency_seconds_bucket = Histogram('llm_shield_latency_seconds_bucket', 'Request latency')
