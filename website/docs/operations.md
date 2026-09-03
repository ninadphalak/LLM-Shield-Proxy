# Operations Runbook

This guide covers operational signals, monitoring, and incident response.

## 1. Prometheus Alerting Rules

LLM-Shield-Proxy exposes Prometheus metrics at `/metrics`. Define alert thresholds based on your deployment's baseline.

| Metric | Condition to Alert | Mitigation |
| :--- | :--- | :--- |
| `llm_shield_latency_ms` | Exceeds established P99 SLO | Check CPU saturation and detector load. Scale proxy replicas. |
| `llm_shield_redaction_count_total` | Spikes above baseline | Investigate traffic mix or malformed clients. Could indicate bulk data exfiltration attempt. |
| `redis_connection_errors_total` | `> 0` | Check Redis network connectivity and authentication. |
| `llm_shield_agent_breaker_trips` | Spikes above baseline | Review session IDs; agents may be stuck in polling loops or repeatedly triggering tool failures. |

## 2. Redis Sizing

If you use Redis for stateful PII mapping (`REDIS_URL`):
- Provision adequate RAM on the external Redis cluster.
- Monitor `used_memory`, `used_memory_rss`, and eviction rates.
- **Set a strict TTL:** Configure `SESSION_TTL_SECONDS` to automatically expire mappings and reclaim memory based on your maximum required chat session length.

## 3. Incident Response for Data Leaks

If you suspect sensitive data leaked to an upstream provider:
1. **Preserve Evidence Safely:** Do not copy or paste the suspected PII into plaintext ticketing systems (e.g., Jira, Slack).
2. **Review Audit Logs:** Retrieve the hash-chained audit events for the relevant timeframe.
3. **Verify Signatures:** Use `llm-shield-proxy audit-checkpoint-verify` to confirm the audit logs haven't been tampered with.
4. **Identify the Caller:** Check the authenticated client identity, `X-Request-ID`, and IP address in the verified audit log to determine the source of the leak.
