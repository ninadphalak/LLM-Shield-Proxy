[⬅️ Back to README](README.md)

# 📜 Compliance: Audit, Forensics & Legal

LLM-Shield-Proxy is an enterprise **AI Gateway** and **LLM Firewall** designed specifically to help engineering teams adopt Generative AI and maintain continuous **AI Governance** without violating data privacy regulations. Operating as a zero-egress, stateless middleware proxy deployed entirely within your own Virtual Private Cloud (VPC), it inherently bypasses the major compliance risks associated with third-party SaaS security tools and enforces robust **LLM Security Posture Management (LLM SPM)**.

This document maps LLM-Shield's architectural features directly to standard enterprise compliance frameworks and strict forensic controls.

## 🛡️ SOC 2 Type II & HITRUST Audit Capabilities

To satisfy the strict Privacy, Security, and Confidentiality criteria required by B2B SaaS and healthcare SOC 2 audits, LLM-Shield-Proxy employs military-grade cryptographic logging and availability controls.

### 🛡️ SOC 2 Compliance for AI & ISO 42001 AI Management System
If you are deploying LLM-Shield to satisfy a compliance audit for **SOC 2 Compliance for AI** or the **ISO 42001 AI Management System**, map the proxy's features directly to your Trust Services Criteria:
* **CC6.1 (Logical Access):** Satisfied by [Pluggable Tool-Call RBAC](docs/PLUGGABLE_RBAC_ENGINE.md).
* **CC6.6 (Boundary Protection):** Satisfied by Zero-Egress Synthetic Masking.
* **CC7.2 (Security Monitoring):** Satisfied by the Merkle-Attested OTel Trace Exporter.

### Cryptographic Audit & Tamper Evidence
* **WORM-Compliant Merkle Attestation & Audit Logging**: Emits structured compliance events containing precise timestamps, tenant IDs, and session metadata into write-once-read-many (WORM) storage.
* **Cryptographic SHA-256 Hash Chaining**: Every log entry cryptographically signs and chains to the previous record's hash (`current_hash = SHA256(prev_hash + timestamp + tenant_id + payload_diff)`). This guarantees tamper-evidence; any post-facto modification instantly invalidates the chain.
* **Cryptographic Proof of Non-Egress Merkle Attestation**: Constructs runtime Merkle trees over redacted streaming chunks. This provides mathematical proof that no unredacted chunks escaped the VPC boundary, satisfying strict auditor verification requirements.
* **Flags**: [`TELEMETRY_ENABLED`](DEPLOYMENT.md#advanced-feature-flags-compliance-security-and-engineering), [`AUDIT_LOG_FORMAT`](DEPLOYMENT.md#advanced-feature-flags-compliance-security-and-engineering)

### Availability, Reliability, and Resiliency (SOC 2 Security Principles)
* **Composite Agent Loop Circuit Breaker (Autonomous Agent Security)**: Actively tracks `tool_calls` array depths and initiates a deterministic circuit break against runaway autonomous agents, ensuring strict **Autonomous Agent Security** by preventing API billing exhaustion and DoS attacks.
* **Zero-Allocation Streaming JSON Lexer**: Defends against memory ballooning and Slowloris attacks by using a Rust-backed `orjson` lexer, strictly bounding memory utilization below 60MB.
* **Traffic Engineering & Resiliency**: Employs a Redis `evalsha` Token-Bucket Rate Limiter (6000 RPM / 200 Burst) alongside Kubernetes 25s SIGTERM connection draining to ensure high availability and graceful degradation.
* **Deep Component Health Probes**: Integrates `/healthz`, `/livez`, and `/readyz` probes with Prometheus Alert Rules to constantly monitor Redis and Vault connectivity.
* **Flags**: [`ENABLE_AGENT_BREAKER`](DEPLOYMENT.md#advanced-feature-flags-compliance-security-and-engineering), [`ENABLE_RATE_LIMITING`](DEPLOYMENT.md#advanced-feature-flags-compliance-security-and-engineering), [`MAX_SSE_LINE_LENGTH`](DEPLOYMENT.md#core-configuration-flags)

### Data-in-Transit Encryption & FIPS Integrity
* **FIPS 140-3 KAT**: All cryptographic modules run FIPS 140-3 Known Answer Tests (KAT) at startup to verify integrity.
* **Centralized Enterprise Secrets & mTLS**: Features native HashiCorp Vault (AppRole / K8s / Token) integration with non-blocking TTL caching. Enforces strict X.509 mTLS transport for backend secret retrieval.
* **Flags**: [`FIPS_STRICT_MODE`](DEPLOYMENT.md#advanced-feature-flags-compliance-security-and-engineering), [`ENABLE_MTLS`](DEPLOYMENT.md#advanced-feature-flags-compliance-security-and-engineering), [`ENABLE_VAULT_SECRETS`](DEPLOYMENT.md#advanced-feature-flags-compliance-security-and-engineering)

## 🕵️ Insider Forensics & Tracing

### Dynamic Canary Watermarking & Steganography
* **Implementation Details**: Injects cryptographically verifiable, invisible canary tokens (via zero-width Unicode characters representing `HMAC_SHA256(Tenant_ID + Timestamp + Virtual_Key)`) into the output stream. This allows exact forensic tracing and attribution of leaked screenshots or text payloads, prosecuting insider model leaks.
* **Flags**: [`ENABLE_WATERMARKING`](DEPLOYMENT.md#advanced-feature-flags-compliance-security-and-engineering), [`SHIELD_WATERMARK_SECRET`](DEPLOYMENT.md#advanced-feature-flags-compliance-security-and-engineering)

### Lightweight OpenTelemetry (OTel) Tracing
* **Implementation Details**: Implements zero-overhead W3C `traceparent` distributed tracing propagation via a dedicated asynchronous background thread, allowing SOC analysts to track complete request lifecycles without degrading LLM response latency.
* **Flags**: [`TELEMETRY_ENABLED`](DEPLOYMENT.md#advanced-feature-flags-compliance-security-and-engineering), [`TELEMETRY_ENDPOINT_URL`](DEPLOYMENT.md#advanced-feature-flags-compliance-security-and-engineering)

## 🌐 Secure Mesh Architecture
To ensure the proxy itself cannot be bypassed by malicious internal developers, LLM-Shield leverages natively secure infrastructure patterns:
* **Zero-Dependency Kubernetes Mutating Webhook**: Transparently intercepts Pod deployment manifests to inject the LLM-Shield sidecar container, ensuring coverage without developer opt-in.
* **Service Mesh Native gRPC ext_proc Integration**: Operates over Zero HTTP network hops by streaming buffers directly via UDS (Unix Domain Sockets) within Envoy's `ExternalProcessor` filter.
* **Anthropic Adapter Implementation**: Employs Zero-SDK OpenAI-to-Anthropic request transformations with SSE stream normalization directly at the network edge, ensuring consistent security controls regardless of the backend provider.

## 🏥 Appendix: HIPAA (Health Insurance Portability and Accountability Act)

For healthcare organizations and digital health startups, streaming Protected Health Information (PHI) to external LLM APIs (like OpenAI or Anthropic) without a Business Associate Agreement (BAA) is a direct HIPAA violation. LLM-Shield allows organizations to utilize LLMs safely by deterministically removing PHI before it leaves the hospital's network.

| HIPAA Requirement | Architectural Defense |
| :--- | :--- |
| **Transmission Security**<br>*(45 CFR § 164.312(e)(1))* | **Data Loss Prevention (DLP) for LLMs (Synthetic Masking & Entropy)**: The proxy intercepts outbound payloads locally. Utilizing a math-bound O(N) Tier-2 Shannon Entropy scanner, it performs **Data Loss Prevention (DLP) for LLMs** by detecting unstructured secrets and deterministically substituting them with realistic Faker-based synthetic entities. No raw PHI traverses the public internet.<br>Flags: [`ENABLE_TIER2_ENTROPY`](DEPLOYMENT.md#core-configuration-flags), [`ENABLE_SYNTHETIC_SWAPPING`](DEPLOYMENT.md#core-configuration-flags) |
| **Audit Controls**<br>*(45 CFR § 164.312(b))* | **RFC 6902 Differential Audit Logging**: Generates exact RFC 6902 compliant JSON patch differential logs detailing redacted string indices without ever persisting raw PHI to disk, compatible with SIEMs like Splunk.<br>Flags: [`AUDIT_LOG_FORMAT`](DEPLOYMENT.md#advanced-feature-flags-compliance-security-and-engineering) |
| **Data Integrity & Storage** | **In-Band Stateless Cryptographic Masking**: Avoids permanent databases entirely. Sensitive entities are either encrypted directly in the LLM context using **AES-256-GCM**, or held temporarily in a volatile Redis Vault with strict rolling TTLs and Deterministic HMAC masking.<br>Flags: [`SHIELD_DEFAULT_MASKING_MODE`](DEPLOYMENT.md#advanced-feature-flags-compliance-security-and-engineering), [`SESSION_TTL_SECONDS`](DEPLOYMENT.md#core-configuration-flags) |
| **Person or Entity Authentication** | **Granular Entity Policy Scopes**: Enforces O(1) in-memory tenant profile mapping. Binds incoming requests to department-level profiles via Virtual Keys, defaulting to `FAIL_CLOSED` **Zero Trust AI** policies to prevent unauthorized lateral access and enforce strict **AI Governance**.<br>Flags: [`VALID_VIRTUAL_KEYS`](DEPLOYMENT.md#core-configuration-flags) |
