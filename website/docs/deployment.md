[⬅️ Back to README](/)

# Deployment and Resilience

For visual diagrams of Air-Gapped and VPC setups, refer to the **[Deployment Topologies](/docs/features/deployment-topologies.md)** guide. The **[Air-Gapped Egress Gateway Mode](/docs/features/air-gapped-egress)** guide documents the internal-gateway routing, DNS pinning, TLS, and credential-forwarding boundary in detail.


## 1. Service Mesh Native Interface
* **Implementation details:** Can be deployed beside Kubernetes service-mesh components. Traffic capture, bypass prevention, ports, identities, and added latency depend on the selected Istio/Linkerd and workload configuration.
* **Relevant Flags**: Not explicitly flagged (relies on standard `HOST`/`PORT` socket configuration).

## 2. Bounded Asynchronous OpenTelemetry Tracing
* **Implementation Details**: OpenTelemetry tracing handles W3C `traceparent` propagation through a bounded asynchronous path. Measure its request-path and drop behavior in the selected exporter configuration; asynchronous does not mean zero overhead.
* **Relevant Flags**:
  * [`TELEMETRY_ENABLED`](#advanced-feature-flags-compliance-security-and-engineering)
  * [`TELEMETRY_ENDPOINT_URL`](#advanced-feature-flags-compliance-security-and-engineering)

## 3. Service Mesh Native gRPC ext_proc Integration
* **Implementation Details**: Implements Envoy's External Processing filter (`envoy.service.ext_proc.v3.ExternalProcessor`). Achieves Zero HTTP network hops by streaming buffers directly over UDS (Unix Domain Sockets).
* **Relevant Flags**:
  * [`ENABLE_EXT_PROC`](#advanced-feature-flags-compliance-security-and-engineering)
  * [`EXT_PROC_SOCK_PATH`](#advanced-feature-flags-compliance-security-and-engineering)

## 4. Traffic Engineering & Resiliency
* **Implementation Details**:
  * **Redis evalsha Token-Bucket Rate Limiter**: Pre-loaded Lua scripts handle high-throughput rate limiting (6000 RPM / 200 Burst). Linked to [`ENABLE_RATE_LIMITING`](#advanced-feature-flags-compliance-security-and-engineering), [`RATE_LIMIT_RPM`](#advanced-feature-flags-compliance-security-and-engineering), and [`RATE_LIMIT_BURST`](#advanced-feature-flags-compliance-security-and-engineering).
  * **Kubernetes SIGTERM Connection Draining**: Gives active SSE streams up to the configured drain timeout to finish before process termination. Streams that exceed the timeout can still be interrupted. Linked to [`DRAIN_TIMEOUT_SECONDS`](#advanced-feature-flags-compliance-security-and-engineering).
  * **Upstream Key Overriding**: Strips client keys and injects internal load-balanced provider API keys dynamically. Linked to [`OVERRIDE_CLIENT_AUTH`](#advanced-feature-flags-compliance-security-and-engineering).

## 5. Kubernetes Mutating Webhook
* **Implementation details:** The `/v1/k8s/mutate` route appends one proxy container to Pods labeled `llm-shield.io/inject=true`; it does not change application routing. The deploy Helm chart leaves `webhook.enabled=false` by default. Enabling it mounts a serving certificate and changes the chart's HTTP service to TLS because the webhook and proxy share one FastAPI listener.
* **Relevant settings:** Helm values `webhook.enabled`, `webhook.sidecarImage`, and `webhook.certManager.*`; runtime setting `K8S_SIDECAR_IMAGE`.

## 6. Deep Component Health Probes and Prometheus Alert Rules
* **Implementation Details**: Provides granular `/healthz`, `/livez`, and `/readyz` probes covering Redis connectivity and Vault mTLS states. Integrates directly with Prometheus Alertmanager via pre-packaged alert rules.
* **Relevant Flags**: [`METRICS_BEARER_TOKEN`](#core-configuration-flags).


## 7. ⚙️ Complete 12-Factor Environment Configuration (`pydantic-settings`)
Configuration follows 12-factor environment-variable practices. Upstream routing, keys, thresholds, and pool sizes are managed via validated `pydantic-settings`:

### Hierarchical Policy-as-Code (RBAC)
Operators can mount a `policies.yaml` file to map `virtual_key_id` client identities to supported security roles. A background poller reloads changed policy data on its configured interval. Validate update visibility, parse-error behavior, unknown identities, and in-flight SSE behavior in the selected worker and storage topology.

**Universal Dynamic Override Engine:**
Policies can supply request-scoped overrides for settings that the implementation reads through its dynamic settings proxy. This is not a promise that every `.env` field is safe or effective per request; allowlist and exercise the exact overrides used, including concurrent requests and background tasks.

> **[View the Complete Policy-as-Code Guide & Templates 📜](/docs/policies)**: For detailed Role-Based Access Control (RBAC) templates, feature support matrices, and FAQs.

### Core Configuration Flags

| Environment Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **`HOST`** | `str` | `0.0.0.0` | Socket host to bind |
| **`PORT`** | `int` | `8000` | Socket port to bind |
| **`WORKERS`** | `int` | `1` | Number of worker processes |
| **`LOG_LEVEL`** | `str` | `INFO` | Standard log verbosity level |
| **`UPSTREAM_BASE_URL`** | `str` | `https://api.openai.com` | Target upstream LLM provider base URL |
| **`OPENAI_API_KEY`** | `str` | `None` | Centralized enterprise OpenAI API key |
| **`GEMINI_API_KEY`** | `str` | `None` | Centralized Google Gemini API key |
| **`ANTHROPIC_API_KEY`** | `str` | `None` | Centralized Anthropic API key |
| **`DEEPSEEK_API_KEY`** | `str` | `None` | Centralized DeepSeek API key |
| **`VALID_VIRTUAL_KEYS`** | `str` | `""` | Comma-separated list of authorized client virtual keys |
| **`ENABLE_OPEN_BYOK_PASSTHROUGH`** | `bool` | `False` | Allow a caller presenting an unrecognized but provider-shaped key (`sk-proj-`/`sk-ant-`/`AIza` prefix) to pass through as BYOK without matching `VALID_VIRTUAL_KEYS`. A prefix match alone doesn't authenticate the caller as an entitled proxy user -- with this left at its default, an unrecognized key is rejected `401` instead of being routed through the DLP pipeline and forwarded upstream. |
| **`ALLOW_CLIENT_UPSTREAM_OVERRIDE`** | `bool` | `False` | Allow clients to override upstream URL via header. The SSRF-validated IP is pinned for the socket connection, but TLS SNI/certificate verification still targets the real hostname -- see "TLS SNI Pinning on Dynamic Upstream Override" below. |
| **`CORS_ALLOWED_ORIGINS`** | `str` | `""` | Comma-separated allowed browser origins for preflight requests. Unset/empty is **strict-by-default**: `Access-Control-Allow-Origin: null` (cross-origin access denied) rather than reflecting the caller's `Origin` or falling back to `*`. Set to `*` to explicitly opt into reflecting any origin, or list specific origins to allowlist them. |
| **`REDIS_URL`** | `str` | `None` | Redis connection URL for distributed vault state |
| **`SESSION_TTL_SECONDS`** | `int` | `3600` | Rolling TTL in seconds for session vault states |
| **`MAX_SESSION_VAULTS`** | `int` | `10000` | Maximum in-memory LRU session vault capacity |
| **`ENABLE_SYNTHETIC_SWAPPING`**| `bool` | `True` | Enables realistic synthetic entity replacement |
| **`ENABLE_TIER2_ENTROPY`** | `bool` | `True` | Enables Tier 2 Shannon Entropy detection |
| **`SHANNON_ENTROPY_THRESHOLD`** | `float` | `4.5` | Minimum information entropy threshold |
| **`SHANNON_MIN_LENGTH`** | `int` | `16` | Minimum token length to analyze for Shannon entropy |
| **`ENABLE_TIER3_ONNX_NER`** | `bool` | `False` | Enables Tier 3 ONNX Runtime contextual NER |
| **`ONNX_MODEL_PATH`** | `str` | `None` | Path to quantized ONNX BERT-NER model weights |
| **`CUSTOM_REGEX_PATH`** | `str` | `None` | Path to `custom_regex.yaml` containing BYOR rules |
| **`HTTP_TIMEOUT_SECONDS`** | `float` | `120.0` | Upstream HTTP request timeout in seconds |
| **`HTTP_MAX_KEEPALIVE_CONNECTIONS`** | `int` | `10000` | Maximum keep-alive connections in HTTP pool |
| **`MAX_PAYLOAD_SIZE_BYTES`** | `int` | `10485760` | Maximum request body size; also contributes to the post-rehydration output-piece ceiling |
| **`MAX_SSE_LINE_LENGTH`** | `int` | `1048576` | Maximum unparsed upstream SSE accumulator and output-coalescing target; one rehydrated line may exceed the target, subject to the absolute output-piece ceiling |
| **`METRICS_BEARER_TOKEN`** | `str` | `None` | Bearer token protecting the `/metrics` endpoint |

### Advanced Feature Flags (Compliance, Security, and Engineering)

| Feature / System | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| **In-Band Stateless Synthetic** | `SHIELD_DEFAULT_MASKING_MODE` | `SYNTHETIC` | Set to `STATELESS_CRYPTO` to enable AES-256-GCM masking. |
| **In-Band Stateless Synthetic** | `SHIELD_ENCRYPTION_KEY` | `None` | 256-bit AES-GCM encryption key for stateless masking. |
| **Audit, Forensics & Legal** | `AUDIT_LOG_FORMAT` | `STANDARD` | Set to `RFC6902_DIFF` for RFC 6902 Differential Audit Logging. |
| **Audit durability** | `AUDIT_DURABILITY` | `best_effort` | `best_effort`, `durable`, or `required`. Durable modes wait for persistence acknowledgement. |
| **Audit durability** | `AUDIT_DURABLE_PATH` | `None` | Required in durable modes. Append-only JSONL path; supports `{instance_id}` and `{pid}` tokens. |
| **Audit durability** | `AUDIT_DURABLE_FSYNC` | `True` | Flush and request `fsync` before acknowledging each durable record. |
| **Audit durability** | `AUDIT_ENQUEUE_TIMEOUT_SECONDS` | `5` | Maximum wait for durable queue and persistence acknowledgement. |
| **Audit signing** | `AUDIT_SIGNING_PRIVATE_KEY` | `None` | Stable Ed25519 PEM/base64/hex private material. Unset keys are ephemeral and unsuitable for cross-restart verification. |
| **Audit signing** | `AUDIT_SIGNING_KEY_FILE` | `None` | Preferred production option. Path to a secret-manager-mounted Ed25519 key; takes precedence and fails startup if unreadable or invalid. |
| **Audit, Forensics & Legal** | `FIPS_STRICT_MODE` | `True` | Strict fail-closed validation for FIPS 140-3 KAT tests. |
| **Agent Circuit Breaker** | `ENABLE_AGENT_BREAKER` | `True` | Enable the duplicate-request loop breaker. |
| **Agent Circuit Breaker** | `AGENT_BREAKER_THRESHOLD` | `3` | Consecutive duplicate turns before tripping the circuit breaker. |
| **Agent Identity Enforcer** | `AGENT_IDENTITY_ENFORCER` | `"off"` | Agent Identity Enforcer mode (`"off"`, `"lenient"`, `"strict"`). |
| **Output correlation** | `ENABLE_WATERMARKING` | `False` | Add a keyed zero-width correlation marker to configured output. |
| **Leak Forensics** | `SHIELD_WATERMARK_SECRET` | `None` | Operator secret for HMAC-SHA256 watermark and identity fingerprints; required when watermarking or canary tripwires are enabled. |
| **MCP RBAC** | `MCP_EMPTY_ALLOWLIST_MODE` | `DENY_ALL` | `DENY_ALL` rejects every tool when `allowed_tools` is empty. `BLOCKLIST_ONLY` explicitly permits tools not named in `blocked_tools` and emits a critical startup warning. |
| **OTel & Tracing** | `TELEMETRY_ENABLED` | `False` | Enable W3C traceparent distributed telemetry export. |
| **OTel & Tracing** | `TELEMETRY_ENDPOINT_URL` | `None` | Target webhook endpoint URL for audit telemetry. |
| **OTel & Tracing** | `ANONYMOUS_USAGE_TRACKING` | `True` | Enable anonymous, opt-out volumetric telemetry. |
| **Tripwire** | `ENABLE_CANARY_TRIPWIRE` | `False` | Enable deterministic prompt-extraction tripwire. |
| **Tripwire** | `CANARY_TOKEN` | `None` | Cryptographic canary string, auto-generated if unset. |
| **Entity request limits** | `ENABLE_BLAST_RADIUS_LIMITS` | `False` | Apply the configured token bucket to detected entity counts. |
| **Blast Radius Limits** | `BLAST_RADIUS_BURST_CAPACITY` | `100` | Maximum bucket size for PII entity exfiltration limit. |
| **Blast Radius Limits** | `BLAST_RADIUS_REPLENISH_RATE_PER_MIN` | `10` | Tokens added back per minute to the bucket. |
| **FinOps Metering** | `ENABLE_FINOPS_METERING` | `True` | Enable token metering and FinOps telemetry. |
| **gRPC ext_proc Mesh** | `ENABLE_EXT_PROC` | `True` | Enable Envoy ext_proc gRPC hook. |
| **gRPC ext_proc Mesh** | `EXT_PROC_SOCK_PATH` | `/var/run/llm-shield/ext_proc.sock` | Path to the ext_proc UDS socket. |
| **Policy-as-Code (RBAC)** | `POLICIES_FILE_PATH` | `policies.yaml` | Path to the hierarchical RBAC YAML policy definitions. |
| **Policy-as-Code (RBAC)** | `POLICIES_RELOAD_INTERVAL_SECONDS` | `5` | File modification polling interval for zero-downtime hot-reloads. |
| **Fail-Safe Policy** | `SHIELD_FAILURE_MODE` | `FAIL_CLOSED` | Enforces Zero-Trust default (O(1) in-memory mapping FAIL_CLOSED). |
| **Anthropic Adapter** | `DEFAULT_UPSTREAM_PROVIDER` | `openai` | Set to `anthropic` for native OpenAI-to-Anthropic request transformation. |
| **Anthropic Adapter** | `ANTHROPIC_API_VERSION` | `2023-06-01` | Anthropic API version header for SSE stream normalization. |
| **Traffic Engineering** | `ENABLE_RATE_LIMITING` | `False` | Enable distributed Redis evalsha Token Bucket rate limiter. |
| **Traffic Engineering** | `RATE_LIMIT_RPM` | `6000` | Requests per minute per virtual key (6000 RPM). |
| **Traffic Engineering** | `RATE_LIMIT_BURST` | `200` | Maximum burst size for rate limiter (200 Burst). |
| **Traffic Engineering** | `DRAIN_TIMEOUT_SECONDS` | `25` | Kubernetes 25s SIGTERM connection draining. |
| **Traffic Engineering** | `OVERRIDE_CLIENT_AUTH` | `False` | Strip client auth and inject UPSTREAM_API_KEY. |
| **Resiliency & Failover** | `ENABLE_RETRY_FAILOVER` | `True` | Enable upstream retry and explicit failover logic. |
| **Resiliency & Failover** | `MAX_RETRIES` | `3` | Maximum transient retry attempts. |
| **Resiliency & Failover** | `FALLBACK_BASE_URL` | `None` | Global fallback provider URL. |
| **Resiliency & Failover** | `FALLBACK_API_KEY` | `None` | Fallback provider API key. |
| **Vault Secrets & mTLS** | `ENABLE_VAULT_SECRETS` | `False` | Enable HashiCorp Vault dynamic secrets. |
| **Vault Secrets & mTLS** | `VAULT_ADDR` | `None` | Vault server address. |
| **Vault Secrets & mTLS** | `VAULT_AUTH_METHOD` | `TOKEN` | Native HashiCorp Vault Auth (AppRole / KUBERNETES / TOKEN). |
| **Vault Secrets & mTLS** | `VAULT_TOKEN` / `VAULT_ROLE_ID` | `None` | Direct Vault Token or AppRole Role ID. |
| **Vault Secrets & mTLS** | `VAULT_REFRESH_INTERVAL_SECONDS`| `300` | Non-blocking TTL cache refresh interval. |
| **Vault Secrets & mTLS** | `ENABLE_MTLS` | `False` | Enable mutual TLS X.509 transport. |
| **Vault Secrets & mTLS** | `SSL_CLIENT_CERT_PATH` | `None` | Path to mTLS client certificate. |
| **TLS/SSL (Inbound)** | `TLS_CERT_FILE` | `None` | Server public certificate. |
| **TLS/SSL (Inbound)** | `TLS_KEY_FILE` | `None` | Server private key. |
| **TLS/SSL (Inbound mTLS)** | `CLIENT_CA_FILE` | `None` | CA bundle for verifying incoming clients. |
| **TLS/SSL (Outbound)** | `CA_BUNDLE_FILE` | `None` | CA bundle for verifying upstream LLM API gateways. |
| **TLS/SSL (Outbound)** | `INSECURE_SKIP_VERIFY` | `False` | Bypass upstream certificate verification. |
| **TLS/SSL (Outbound mTLS)** | `OUTBOUND_CLIENT_CERT` | `None` | Client certificate for proxy outbound mTLS. |
| **TLS/SSL (Outbound mTLS)** | `OUTBOUND_CLIENT_KEY` | `None` | Client key for proxy outbound mTLS. |

> [!WARNING]
> **Failure default (`SHIELD_FAILURE_MODE`)**: The redaction path defaults to `FAIL_CLOSED`, so handled engine failures terminate the affected request instead of forwarding it unchanged. Confirm the behavior of every enabled dependency and policy resolver in failure tests. `FAIL_OPEN` can forward untransformed data after an error and is unsuitable for a production privacy boundary.
## 8. 🔐 TLS and mTLS Deployment (Docker/Kubernetes)

When deploying LLM-Shield-Proxy in production, you should mount TLS certificates securely.
In Kubernetes, use `Secrets` mounted as volumes.

**Example Docker Compose with TLS:**
```yaml
services:
  llm-shield:
    image: llm-shield-proxy:latest
    ports:
      - "8443:8000"
    volumes:
      - ./certs:/etc/ssl/llm-shield:ro
    environment:
      - TLS_CERT_FILE=/etc/ssl/llm-shield/server.crt
      - TLS_KEY_FILE=/etc/ssl/llm-shield/server.key
      - CLIENT_CA_FILE=/etc/ssl/llm-shield/client-ca.pem
      - CA_BUNDLE_FILE=/etc/ssl/llm-shield/upstream-ca.pem
      - OUTBOUND_CLIENT_CERT=/etc/ssl/llm-shield/proxy-client.crt
      - OUTBOUND_CLIENT_KEY=/etc/ssl/llm-shield/proxy-client.key
```
This configuration secures the listener with HTTPS, mandates inbound mTLS (`CLIENT_CA_FILE`), and enables outbound mTLS towards the upstream provider.

### TLS SNI Pinning on Dynamic Upstream Override

When `ALLOW_CLIENT_UPSTREAM_OVERRIDE=True` or `AIR_GAPPED_MODE` is active, the proxy resolves the
target hostname and pins the connection to the checked IP. This prevents a second DNS lookup from
changing the destination before the connection. For TLS, the proxy passes the original hostname
through `extensions={"sni_hostname": ...}` so certificate verification still uses the domain:

- The socket connects to the pinned, SSRF-validated IP.
- TLS SNI negotiation and certificate hostname verification happen against the real domain.

This happens automatically on paths that pin a resolved IP. It does **not** apply to
`FALLBACK_BASE_URL` or `X-Shield-Fallback-URL`; those failover targets use normal hostname-based
TLS.
