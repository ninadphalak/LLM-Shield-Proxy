[⬅️ Back to README](../README.md)

# 🚑 Troubleshooting & Configuration Guide

This guide helps you resolve common configuration errors and understand the proxy's "Fail Closed" design principles when deploying LLM-Shield-Proxy locally or in production.

## Why is my proxy failing to start or dropping traffic?

LLM-Shield-Proxy is engineered as **Critical Compliance Infrastructure**. By design, it operates on a strict **Fail Closed** principle. This means if any configuration is missing, incorrect, or if a backend dependency (like Redis) goes down, the proxy will aggressively drop traffic rather than risking a data leak by failing open.

### Common Fail Closed Configurations

#### 1. `TELEMETRY_ENDPOINT_URL`
If you have configured a telemetry endpoint for shipping Merkle logs and the endpoint becomes unreachable, the proxy will halt traffic.
*   **Production:** This ensures that no traffic is processed without an immutable audit trail.
*   **Local/POC Testing:** If you are just testing the proxy locally, you can disable this by unsetting `TELEMETRY_ENDPOINT_URL` or setting `FAIL_OPEN_ON_TELEMETRY_ERROR=True` (NOT RECOMMENDED for production).

#### 2. Redis Connection Drops
If `SHIELD_DEFAULT_MASKING_MODE=STATEFUL` and the Redis vault goes offline, the proxy cannot map synthetic entities. It will return a `503 Service Unavailable` or `429 Too Many Requests` (to trigger client retries) rather than passing unmasked PII.
*   **Fix:** Ensure `REDIS_URL` is correct and network policies allow traffic. If you don't want to use Redis for testing, switch to `SHIELD_DEFAULT_MASKING_MODE=STATELESS_CRYPTO` to use AES-256-GCM entirely in-memory.

#### 3. Missing `UPSTREAM_API_KEY`
The proxy intercepts the stream but does not hold the ultimate LLM API key. If `UPSTREAM_API_KEY` is not provided in the environment or passed via the Authorization header, it drops the request immediately.

---



## 🏗️ Kubernetes & Helm Chart Troubleshooting

### The `readOnlyRootFilesystem` Warning

> [!WARNING]
> **Production Hardening Note:** Our official Helm chart sets `readOnlyRootFilesystem: true` in the pod `securityContext` by default. This is a critical production hardening step to prevent container breakouts or malicious file drops.

Because LLM-Shield-Proxy uses strictly in-memory processing and writes all logs to `stdout`, it natively supports a read-only filesystem.

**If you encounter `Read-only file system` errors:**
This only happens if you have modified the proxy to write local files (like SQLite databases or temporary file logs). If you implement custom local logging, you must mount an `emptyDir` or Persistent Volume to `/tmp` or `/app/logs` in your deployment, or disable `readOnlyRootFilesystem` in the `values.yaml` (not recommended).
