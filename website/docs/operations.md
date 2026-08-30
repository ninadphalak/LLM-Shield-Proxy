[⬅️ Back to README](/)

# 🛠️ Day-2 Operations & Production Runbook

LLM-Shield-Proxy is designed for zero-trust, highly regulated environments. This runbook provides Platform Engineers and Site Reliability Engineers (SREs) with the exact formulas and metrics needed to monitor the proxy at scale.

## 📊 1. Prometheus Alerting Rules

The proxy natively exposes OpenTelemetry/Prometheus metrics at `/metrics` (or on port `8000` depending on your Helm configuration). Configure your Datadog, Grafana, or Prometheus Alertmanager with the following critical alerts:

| Metric / Alert Rule | Threshold (P99) | Description & Mitigation |
| :--- | :--- | :--- |
| `llm_shield_latency_ms` | Operator-defined SLO | Internal proxy processing exceeded the threshold validated for this deployment. **Mitigation:** Check CPU saturation, detector configuration, audit mode, and event-loop lag; scale replicas when appropriate. |
| `llm_shield_redaction_count_total` | `> 300% anomaly` | A sudden, massive spike in redacted entities. **Mitigation:** This usually indicates an upstream agent has gone rogue and is dumping raw databases into the prompt, or an active exfiltration attempt. Trigger automated SOC alert. |
| `redis_connection_errors_total` | `> 0` | The proxy cannot reach the ephemeral Redis Vault. **Mitigation:** If using `STATEFUL` mode, the proxy operates on a `FAIL_CLOSED` principle and will drop traffic to prevent data leaks. Check Redis network policies. |
| `llm_shield_agent_breaker_trips` | `> 10 / min` | The Composite Agent Loop Circuit Breaker is actively halting autonomous agents (like AutoGen or CrewAI) that are stuck in recursive loops. |

## 💾 2. Empirical Redis Vault Sizing Guide

If you are using the Stateful Redis Vault (`REDIS_URL`), provision enough RAM on your **external Redis server**. Measure proxy RSS separately because the two processes have different memory behavior.

Size Redis from measured bytes per active mapping, mappings per session, concurrent sessions, allocator overhead, replication, persistence, and failover headroom. Capture `used_memory`, `used_memory_rss`, evictions, and key counts during a workload-shaped soak test; do not use a universal per-session estimate.

> [!TIP]
> **TTL Eviction is Mandatory.** Ensure your `SESSION_TTL_SECONDS` is set to match your maximum expected conversational context window (e.g., 3600 seconds for 1 hour). Redis will automatically sweep expired session maps, keeping your memory footprint flat regardless of overall traffic volume.

## 🔍 3. Incident Response & Log Forensics

If a data leak is suspected, Security Analysts must trace the payload without the proxy actually logging the sensitive data (which would violate compliance).

1.  **Extract the Canary:** Check the leaked output for the Dynamic Canary Watermark (injected via zero-width characters or specific deterministic synthetics).
2.  **Query the audit chain:** Query your centralized log aggregator for the hash-chained, signed records emitted by the proxy and verify continuity against a trusted key and external anchor.
3.  **Correlate the Hash:** Search for the `_ctx_hash_prop` or the HMAC-SHA256 hash found in the watermark.
4.  **Identify the Actor:** The structured log will reveal the exact `WorkloadIdentity` (JWT sub), Timestamp, and IP that originated the request, proving egress provenance without ever revealing the plaintext PII in the logs.
