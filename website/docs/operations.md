[⬅️ Back to README](/)

# Operations Runbook

This runbook lists operational signals and checks for LLM-Shield-Proxy. The example thresholds
are starting points only. Set alert conditions from the deployment's measured baseline, SLOs, and
failure tests.

## 1. Prometheus alerting rules

The application exposes Prometheus metrics at `/metrics` on the configured service listener.
OpenTelemetry export uses separate settings. Protect both endpoints and verify the metric names
present in the running version.

| Metric / Alert Rule | Threshold (P99) | Description & Mitigation |
| :--- | :--- | :--- |
| `llm_shield_latency_ms` | Operator-defined SLO | Internal proxy processing exceeded the threshold validated for this deployment. **Mitigation:** Check CPU saturation, detector configuration, audit mode, and event-loop lag; scale replicas when appropriate. |
| `llm_shield_redaction_count_total` | Deployment baseline | An increase can come from traffic mix, detector changes, malformed clients, bulk data, or an attack. Compare request volume and recent configuration before assigning a cause. |
| `redis_connection_errors_total` | `> 0` | A Redis path reported a connection error. **Mitigation:** Check Redis network policy and exercise the exact masking, rate-limit, blast-radius, RBAC, and failure-mode paths in use; they do not all share one fallback behavior. |
| `llm_shield_agent_breaker_trips` | Deployment baseline | Counts breaker decisions after the configured repeated-action threshold. Review session IDs and request patterns; a trip can be a legitimate retry or polling loop. |

## 2. Redis sizing

If you are using the Stateful Redis Vault (`REDIS_URL`), provision enough RAM on your **external Redis server**. Measure proxy RSS separately because the two processes have different memory behavior.

Size Redis from measured bytes per active mapping, mappings per session, concurrent sessions, allocator overhead, replication, persistence, and failover headroom. Capture `used_memory`, `used_memory_rss`, evictions, and key counts during a workload-shaped soak test; do not use a universal per-session estimate.

> [!TIP]
> **Choose the TTL from the required recovery and retention window.** Redis expiry makes mappings
> eligible for removal. Memory reclamation, persistence, replicas, backups, and eviction timing
> depend on the Redis deployment and traffic profile.

## 3. Incident response and log review

If a data leak is suspected, use the proxy's metadata without copying the suspected protected
value into new logs.

1. **Preserve the output safely:** Store suspected content in the approved incident system. Do not
   paste protected values into ordinary logs or tickets.
2. **Check configured markers:** If zero-width marking was enabled, attempt to decode the marker
   and compare it with retained metadata. A match is a correlation signal, not proof of identity.
3. **Verify the audit evidence:** Verify signatures and chain continuity against the trusted public
   key and any external anchor. Missing or unanchored evidence limits the conclusion.
4. **Correlate recorded identity:** Review the workload identity, timestamp, request ID, and network
   metadata. Confirm authentication, proxy trust, clock synchronization, log completeness, and key
   custody before assigning responsibility.
