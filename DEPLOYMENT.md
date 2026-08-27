[⬅️ Back to README](README.md)

# 🚀 Deployment: Infrastructure & Resiliency

## 1. Service Mesh Native Interface
* **Implementation Details**: Integrates gracefully into Kubernetes Service Meshes (like Istio/Linkerd) natively without secondary sidecar bottlenecks, providing seamless inbound/outbound interception to maintain your **LLM Security Posture Management (LLM SPM)**.
* **Relevant Flags**: Not explicitly flagged (relies on standard `HOST`/`PORT` socket configuration).

## 2. Zero-Overhead OpenTelemetry Tracing
* **Implementation Details**: Lightweight OpenTelemetry (OTel) Tracing handles W3C traceparent distributed tracing propagation via a dedicated asynchronous background thread. Provides full observability to Jaeger or Datadog with strictly zero latency overhead to the active HTTP streaming loop.
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
  * **Kubernetes 25s SIGTERM Connection Draining**: Ensures active SSE streams finish transmission during pod termination before tearing down the socket. Linked to [`DRAIN_TIMEOUT_SECONDS`](#advanced-feature-flags-compliance-security-and-engineering).
  * **Upstream Key Overriding**: Strips client keys and injects internal load-balanced provider API keys dynamically. Linked to [`OVERRIDE_CLIENT_AUTH`](#advanced-feature-flags-compliance-security-and-engineering).

## 5. Zero-Dependency Kubernetes Mutating Webhook
* **Implementation Details**: Intercepts Pod deployment manifests directly via a standalone Mutating Webhook to seamlessly inject the LLM-Shield sidecar container and mTLS certificates, requiring zero external dependencies.
* **Relevant Flags**: N/A (Handled via Kubernetes MutatingAdmissionWebhook manifests).

## 6. Deep Component Health Probes and Prometheus Alert Rules
* **Implementation Details**: Provides granular `/healthz`, `/livez`, and `/readyz` probes covering Redis connectivity and Vault mTLS states. Integrates directly with Prometheus Alertmanager via pre-packaged alert rules.
* **Relevant Flags**: [`METRICS_BEARER_TOKEN`](#core-configuration-flags).


## 7. ⚙️ Complete 12-Factor Environment Configuration (`pydantic-settings`)
100% compliant with 12-factor app standards. All upstream target routing, keys, thresholds, and pool sizes are managed via validated `pydantic-settings`:

### Hierarchical Policy-as-Code (RBAC)
DevOps teams can now mount a `policies.yaml` file to dynamically map `virtual_key_id` client identities to distinct security roles. The proxy features a zero-downtime hot-reloading mechanism that continuously polls the file (defaulting to every 5 seconds) and applies modifications immediately without dropping active Server-Sent Event (SSE) streams. Unknown identifiers strictly enforce a Zero-Trust `FAIL_CLOSED` default.

**Universal Dynamic Override Engine:**
DevOps teams are no longer limited to basic security toggles; they can now dynamically override any of the 30+ `.env` properties (like `MAX_PAYLOAD_SIZE_BYTES` or `RATE_LIMIT_RPM`) natively per `virtual_key_id` inside `policies.yaml`. This is powered by an ASGI-native `contextvars.ContextVar` architecture to achieve strictly isolated, O(1) thread-safe tenant configurations without global state leakage.

> **[View the Complete Policy-as-Code Guide & Templates 📜](POLICIES.md)**: For detailed Role-Based Access Control (RBAC) templates, feature support matrices, and FAQs.

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
| **`ALLOW_CLIENT_UPSTREAM_OVERRIDE`** | `bool` | `False` | Allow clients to override upstream URL via header |
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
| **`MAX_PAYLOAD_SIZE_BYTES`** | `int` | `10485760` | Maximum allowed request body size |
| **`MAX_SSE_LINE_LENGTH`** | `int` | `1048576` | Maximum allowed SSE line size (1MB) |
| **`METRICS_BEARER_TOKEN`** | `str` | `None` | Bearer token protecting the `/metrics` endpoint |

### Advanced Feature Flags (Compliance, Security, and Engineering)

| Feature / System | Environment Variable | Default | Description |
| :--- | :--- | :--- | :--- |
| **In-Band Stateless Crypto** | `SHIELD_DEFAULT_MASKING_MODE` | `SYNTHETIC` | Set to `STATELESS_CRYPTO` to enable AES-256-GCM masking. |
| **In-Band Stateless Crypto** | `SHIELD_ENCRYPTION_KEY` | `None` | 256-bit AES-GCM encryption key for stateless masking. |
| **Audit, Forensics & Legal** | `AUDIT_LOG_FORMAT` | `STANDARD` | Set to `RFC6902_DIFF` for RFC 6902 Differential Audit Logging. |
| **Audit, Forensics & Legal** | `FIPS_STRICT_MODE` | `True` | Strict fail-closed validation for FIPS 140-3 KAT tests. |
| **Agent Circuit Breaker** | `ENABLE_AGENT_BREAKER` | `True` | Enable Composite Agent Loop Circuit Breaker. |
| **Agent Circuit Breaker** | `AGENT_BREAKER_THRESHOLD` | `3` | Consecutive duplicate turns before tripping the circuit breaker. |
| **Agent Identity Enforcer** | `AGENT_IDENTITY_ENFORCER` | `"off"` | Agent Identity Enforcer mode (`"off"`, `"lenient"`, `"strict"`). |
| **Leak Forensics** | `ENABLE_WATERMARKING` | `False` | Enable Dynamic Canary Watermarking & Steganography. |
| **Leak Forensics** | `SHIELD_WATERMARK_SECRET` | `None` | Secret for HMAC-SHA256 watermarking. |
| **OTel & Tracing** | `TELEMETRY_ENABLED` | `False` | Enable W3C traceparent distributed telemetry & WORM-Compliant Merkle Logging. |
| **OTel & Tracing** | `TELEMETRY_ENDPOINT_URL` | `None` | Target webhook endpoint URL for audit telemetry. |
| **OTel & Tracing** | `ANONYMOUS_USAGE_TRACKING` | `True` | Enable anonymous, opt-out volumetric telemetry. |
| **Tripwire** | `ENABLE_CANARY_TRIPWIRE` | `False` | Enable deterministic prompt-extraction tripwire. |
| **Tripwire** | `CANARY_TOKEN` | `None` | Cryptographic canary string, auto-generated if unset. |
| **Blast Radius Limits** | `ENABLE_BLAST_RADIUS_LIMITS` | `False` | Enable Entity-Weighted Blast Radius Limits. |
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
> **Zero-Trust Default (`SHIELD_FAILURE_MODE`)**: The proxy is hardcoded to default to `FAIL_CLOSED`. This ensures that if the engine faults, a Redis connection drops, or a policy cannot be resolved, the connection is instantly severed to prevent PII egress. If you must set `SHIELD_FAILURE_MODE=FAIL_OPEN` for local development or a POC, be aware that **PII may leak** if the engine encounters an error. Never use `FAIL_OPEN` in production.
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
