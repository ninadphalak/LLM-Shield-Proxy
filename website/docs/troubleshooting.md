[⬅️ Back to README](/)

# Troubleshooting

This guide explains common startup and traffic failures.

## Common Fail-Closed Configurations

LLM-Shield-Proxy defaults to a strict `FAIL_CLOSED` posture for core security dependencies. If a required dependency is unreachable, the proxy drops the request rather than bypassing the security check.

### 1. Redis Connection Drops
If `SHIELD_DEFAULT_MASKING_MODE=STATEFUL` (the default) and the Redis server becomes unreachable, the proxy cannot safely map or retrieve synthetic entity mappings. It will block requests with a `503 Service Unavailable` or `429 Too Many Requests` error rather than allowing unmasked PII to egress.
* **Fix:** Verify `REDIS_URL` and network connectivity. For local testing without Redis, set `SHIELD_DEFAULT_MASKING_MODE=STATELESS_CRYPTO`.

### 2. Missing Upstream API Key
The proxy does not route traffic if it cannot authenticate with the upstream provider. If `UPSTREAM_API_KEY` is not set in the environment, and the client does not provide an override in the Authorization header, the proxy immediately drops the request.

### 3. Audit Logging Failures
If `AUDIT_DURABILITY` is set to `required` (mandatory for strict compliance) and the local disk is full or the file path is unwritable, the proxy will fail the request.
* **Fix:** Ensure the proxy process has write access to the configured `AUDIT_DURABLE_PATH`. For local development, you can use the default `best_effort` mode which writes to `stdout` and drops logs if the internal queue is full.

## Checking Name Redaction Status
Name (PERSON) redaction requires an ONNX NER model and is otherwise completely inactive. You can verify whether name redaction is active using the health endpoints or the compliance pack:
*   `/health` and `/livez` return `name_redaction: ok` or `name_redaction: unavailable`. 
*   `/readyz` reports the same under `components` and includes a `warnings[]` entry naming the affected policy profiles if inactive.
*   The compliance pack includes a "Redaction Coverage In Force" section and a `redaction_coverage.json` file.
*   **Note:** A status of `name_redaction: unavailable` does not fail readiness checks (the pod stays healthy) because most deployments run without a model.

## Kubernetes Deployment Issues

### The `readOnlyRootFilesystem` Warning
The official Helm chart enforces `readOnlyRootFilesystem: true` in the pod's `securityContext` to prevent container breakout attacks. 

LLM-Shield-Proxy operates entirely in memory and logs to `stdout` by default, making it fully compatible with a read-only filesystem.

**If you encounter "Read-only file system" errors:**
You have likely configured the proxy to write files locally (e.g., setting a local `AUDIT_DURABLE_PATH` for audit logs). You must mount an `emptyDir` or Persistent Volume to that specific path in your Kubernetes deployment. Do not disable `readOnlyRootFilesystem`.
