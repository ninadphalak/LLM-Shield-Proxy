[⬅️ Back to README](/)

# 🏛️ Architecture: Deep Dive into the Data Plane

## 🏗️ Architecture & Cryptographic Data Flow (Executive Summary)

This document describes the proxy's technical mechanics and evidence boundaries. The features can support regulatory controls; they do not establish compliance by themselves.

## Architectural Traffic Flow

The LLM-Shield-Proxy operates as an in-VPC mathematical sanitization layer. The following diagram illustrates the lifecycle of a request from the client through the proxy to the external Large Language Model (LLM) and back, demonstrating the integration of our cryptographic and compliance components.

```text
+-------------+         +-----------------------------------------------------------+          +---------------+
|             | (HTTPS) |                      IN-VPC PROXY                         | (HTTPS)  |               |
|   Client    |=======> |  +-----------------------------------------------------+  |=======>  | External LLM  |
| Application |         |  | 1. Ingress Scanning & 3-Tier Cascade Redaction      |  |          | (OpenAI,      |
|             | <=======|  |    - C++ google-re2 DFA Regex (O(N) linear time)  |  | <======= |  Anthropic,   |
+-------------+  (SSE)  |  |    - Local Shannon Entropy Scanner                 |  |  (SSE)   |  Gemini,      |
                        |  |    - Quantized ONNX BERT-NER (in-memory)            |  |          |  vLLM)        |
                        |  +-------------------------+---------------------------+  |          +---------------+
                        |                            |                              |
                        |  +-------------------------v---------------------------+  |
                        |  | 2. Stateless Data Protection & Zero Liability       |  |
                        |  |    - In-Band AES-256-GCM Envelope Encryption        |  |
                        |  |    - 4-Mode Masking (SYNTHETIC, STRUCTURAL, SCRUB)  |  |
                        |  +-------------------------+---------------------------+  |
                        |                            |                              |
                        |  +-------------------------v---------------------------+  |
                        |  | 3. Signed Audit Evidence & GRC Dispatch             |  |
                        |  |    - SHA-256 Sequential Merkle Hash Chaining        |  |
                        |  |    - RFC 6902 JSON Patch Differential Logs          |  |
                        |  |    - Universal Decision Trace Exporter (NIST OSCAL) |=============> To Vanta, Drata,
                        |  +-------------------------+---------------------------+  |            Datadog
                        |                            |                              |
                        |  +-------------------------v---------------------------+  |
                        |  | 4. Streaming Traffic Engineering & Rehydration      |  |
                        |  |    - Bounded SSE Sliding-Window Buffer              |  |
                        |  |    - Bounded JSON Recursion Parser (Rust orjson)    |  |
                        |  +-------------------------+---------------------------+  |
                        |                            |                              |
                        |  +-------------------------v---------------------------+  |
                        |  | 5. Mapping expiry and eviction                     |  |
                        |  |    - Ephemeral In-Memory Vaults                     |  |
                        |  |    - Zero prompt/PII persistence to disk            |  |
                        |  +-----------------------------------------------------+  |
                        +-----------------------------------------------------------+
```

## The 5-Stage Cryptographic Lifecycle

### Stage 1: Ingress Scanning & 3-Tier Cascade Redaction
Upon ingress, payloads are intercepted by a highly optimized redaction engine:
1. **Tier 1 (Structured Identifiers):** Pre-compiled `google-re2` patterns avoid catastrophic backtracking for supported constructs. The tier handles configured SSNs, emails, IPs, and custom identifiers (`BYOR` - Bring Your Own Regex); unsupported constructs must be rejected or use a documented fallback.
2. **Tier 2 (Unstructured Secrets):** The Shannon entropy scanner identifies secret-like high-entropy candidates. Measure it on the exact payload distribution and do not treat entropy alone as proof of a secret.
3. **Tier 3 (Conversational Entities):** Quantized ONNX BERT-NER models execute natively in-memory (optional NLP mode) for context-aware entity extraction. Supports BYOM (Bring Your Own Model) for specialized architectures like BioBERT, ClinicalBERT, XLM-RoBERTa, and Legal-BERT.

### Stage 2: Stateless Envelope Encryption & Masking
To reduce disclosure of detected PII to a configured upstream while retaining useful payload structure:
- **In-Band Stateless Synthetic:** Entities are masked using AES-256-GCM envelope cryptography directly within the payload.
- **4-Mode Pipeline:** Dynamic per-request masking via headers supports:
  - `SYNTHETIC`: Canonical locale swapping to preserve BPE token lengths.
  - `STRUCTURAL_TAG`: Replacements like `[PERSON_1]`.
  - `SCRUB`: Hard deletion of offending tokens.
  - `STATELESS_CRYPTO`: Encrypted envelopes that can be decrypted when the token, key, algorithm context, and associated data remain valid.

### Stage 3: Tamper-Evident Chaining & Traceability
To support integrity and traceability without retaining prompt data:
- **Audit evidence:** SHA-256 predecessor links and Ed25519 signatures let offline verification detect changes within the supplied chain. Local storage is not WORM; immutable retention and external anchoring are deployment controls.
- **Differential Logging:** Supported audit events can include RFC 6902 operations supplied by the redaction path. Validate exception, debug, custom-rule, exporter, and downstream logging paths for raw-value leakage.
- **Signed Egress Transformation Receipt:** Calculates a digest over configured transformation metadata and emits a signed receipt. It attests to application-generated evidence, not to every packet on the network.

### Stage 4: Sliding-Window SSE Rehydration
For incremental streaming and bounded retained state:
- **SSE Sliding Buffers:** The lookahead buffer retains prefix overlap so placeholders fragmented across SSE events can be rehydrated. Historical component timings are not total proxy overhead; use the conformance and service-level protocols.
- **Bounded Parsers:** Uses `orjson` plus explicit depth/bracket checks on documented paths. These bounds reduce specific recursion/resource risks but are not a complete denial-of-service defense.

### Stage 5: Ephemeral Memory Eviction
To reduce retained masking state:
- **Stateless crypto:** Avoids a mapping database but still handles plaintext in process memory during transformation.
- **In-memory mode:** Keeps mappings in process memory until request/session cleanup, expiry, or process termination.
- **Redis TTL mode:** Makes mappings eligible for expiry in Redis; persistence files, replicas, backups, swap, crash dumps, and snapshots require separate controls.
- **Verification:** Inspect logging, telemetry, audit, exception, container-runtime, and infrastructure paths before making a storage claim for a deployment.


---

## 🔬 Deep Dive Mechanics

LLM-Shield-Proxy is an asynchronous middleware data plane between enterprise applications and upstream models. Component and service-level overhead are reported separately.

The sections below identify the bounded algorithms and native components that should be evaluated in a reproducible deployment benchmark.

## 1. ⚙️ The Data Plane & Streaming Engine

The data path combines Python orchestration with selected native libraries. Throughput, event-loop lag, GIL effects, and process RSS must be measured under the intended payload and concurrency profile.

### Bounded Streaming JSON Lexer (`orjson` / Rust)
* **Implementation Mechanics:** The streaming engine uses `orjson` and processes fragmented SSE events incrementally. The conformance report checks retained-buffer bounds and measured allocation; no universal RSS ceiling is claimed.
* **Flags:** [`MAX_SSE_LINE_LENGTH`](/docs/deployment)

### Resilient SSE Sliding-Window Buffer
* **Implementation Mechanics:** Server-Sent Events can split a placeholder across chunks. `SSERehydrationBuffer` retains trailing characters with the declared bound $LL = max(0, max_token_length - 1)$. The conformance fixtures exercise fragmentation; cancellation, malformed events, scheduling, and backpressure remain separate failure modes.

### Context-Aware MCP Discovery Interception
* **Implementation Mechanics:** To support stateless MCP architecture and progressive discovery, `MCPDiscoveryPrunerMiddleware` evaluates JSON-RPC tool catalogs against RBAC `frozenset` values and emits bounded chunks with ASGI backpressure. Measure process memory under the intended catalog and concurrency distribution.

### Dual-Pipeline Routing & Dynamic Schema Rewriting (Machine-to-Machine)
* **Implementation Mechanics:** In `main.py`, a top-level object with `jsonrpc == "2.0"` takes the AST-aware stateless mutation path. The separate `/v1/mcp` route has a different scoped contract.
* **Detection & substitution:** Supported structured string leaves are inspected with the stateless visitor and protected with AES-GCM context using `SHIELD_ENCRYPTION_KEY`. Dictionary values remain strings with sibling context fields; array values use wrapper objects and can change schema shape.
* **Dynamic Schema Injection:** The proxy can add cryptographic context fields to an OpenAI/MCP tool schema and mark them required. Provider behavior is not mathematically guaranteed; conformance and integration tests must verify echo and rehydration behavior for each selected model/provider.
## 2. 🛡️ The 3-Tier Redaction Cascade

The engine pipelines payload text through three consecutive filters, balancing compute cost against redaction recall.

### Tier 1: DFA Pre-compiled Regex (`google-re2`)
* **Implementation Mechanics:** When available, the `re2` engine compiles supported patterns to avoid catastrophic backtracking. Unsupported constructs and fallback-engine behavior must be checked at startup. Throughput is workload-specific.

### Tier 2: Shannon Entropy

### Step 2: Format-Preserving Synthetic Masking
* **Implementation Mechanics:** Regular expressions fail on unstructured data (e.g., 64-character raw cryptographic keys). The Tier 2 engine computes Shannon entropy `H(S) = -\sum p(c) \log_2 p(c)` across a sliding window. It targets base64 strings with entropy `\ge 4.5` bits/char and hex strings `\ge 3.4` bits/char.
* **Format-aware masking:** Synthetic mode attempts to retain useful syntax for selected entity types. Token counts, model attention, downstream quality, and false-positive effects are workload- and tokenizer-dependent.
* **Flags:** [`ENABLE_TIER2_ENTROPY`](/docs/deployment), [`ENABLE_SYNTHETIC_SWAPPING`](/docs/deployment)

### Step 3: Script-Aware Non-Latin & CJK Rehydration Engine
* **Implementation mechanics:** Word-boundary behavior differs across scripts and token shapes. The proxy uses `_is_ascii_word_char` for one supported boundary check; run the multilingual and fragmentation fixtures for the exact masking mode and corpus because this does not cover every Unicode segmentation case.

## 3. 🔐 Cryptographic Memory Vaults

Stateful masking modes keep original-to-token mappings in process memory or Redis. Stateless crypto avoids that mapping database for supported flows but still processes plaintext in memory and sends ciphertext-derived values upstream.

### Stateless Redis TTL Vault & Deterministic HMAC Masking
* **Implementation mechanics:** In Redis-backed mode, replicas can share mappings addressed with HMAC-derived keys and configured TTLs. Expiry is not secure erasure, and stream disconnect, persistence, replicas, backups, eviction timing, and cleanup failures need explicit tests.
* **Flags:** [`REDIS_URL`](/docs/deployment), [`SESSION_TTL_SECONDS`](/docs/deployment)

### In-Band Stateless Syntheticgraphic Masking
* **Implementation Mechanics:** For organizations without Redis, protected values can be encrypted into the payload using AES-256-GCM. Successful rehydration depends on the upstream returning the protected envelope intact and must be integration-tested with the configured provider.
* **Flags:** [`SHIELD_DEFAULT_MASKING_MODE`](/docs/deployment)

## 4. 🌐 Service Mesh & Multi-Provider Translation

### Multi-Provider Translators & Anthropic Adapter
* **Implementation mechanics:** The Anthropic adapter translates a supported subset of OpenAI-style `messages` requests after configured transformation. Validate tools, multimodal content, errors, streaming, and unsupported fields before relying on it.
* **SSE normalization:** The adapter maps a documented subset of Anthropic `content_block_delta` events into an OpenAI-style delta shape. This does not establish compatibility with every LangChain, LiteLLM, model, tool, or error path.

### Service Mesh Native gRPC `ext_proc` Integration
* **Implementation mechanics:** The Envoy `ext_proc` option sends configured headers and bodies to the processor over gRPC, with a Unix Domain Socket available for same-pod IPC. UDS removes an IP routing hop but still incurs copying, serialization, scheduling, parsing, and transformation work. Validate body modes, buffer limits, timeout/failure policy, and UDS permissions with the selected Envoy version.

## 5. 🛑 Traffic Engineering & Resiliency

LLM-Shield includes optional quota, retry, drain, and heuristic breaker controls. They reduce selected risks but do not prevent every resource-exhaustion condition.

### Composite Agent Loop Circuit Breaker
* **Implementation mechanics:** The circuit breaker tracks documented request/tool-call signals and can return `HTTP 429` when a configured threshold is crossed. It is a heuristic that can miss changing loops or flag legitimate repetition; pair it with provider and application budgets.
* **Flags:** [`ENABLE_AGENT_BREAKER`](/docs/deployment)

### Rate Limiting & Deep Component Health
* **Token-Bucket Rate Limiter:** Utilizing a Redis Lua script (`evalsha`) for atomicity, the proxy enforces a hard 6000 RPM / 200 Burst limit without race conditions.
* **Connection Draining:** Listens for Kubernetes `SIGTERM` signals and initiates a 25-second graceful connection draining period, ensuring existing SSE streams finish before the pod terminates.
* **Health probes:** Exposes `/healthz`, `/livez`, and `/readyz` signals for documented dependencies. Probe freshness, thresholds, and failure coverage depend on configuration and do not replace service-level monitoring.

## 6. ⚔️ Adversarial Defenses & Normalization

Attackers frequently use invisible Unicode characters and encoding tricks to bypass standard regex filters or overwhelm the processing engine.

### Adversarial Desmuggling & Normalization Pipeline
* **Zero-Width Character Stripping:** Filters zero-width spaces (`\u200B`), joiners (`\u200D`), byte order marks, and soft hyphens.
* **BiDi / RTL Override Neutralization:** Strips Right-to-Left Overrides that visually flip character orders to humans while evading byte scanners.
* **NFKC Unicode Normalization:** Converts full-width, circled, and decomposed glyphs to canonical equivalents prior to pattern matching.
* **Base64 Candidate Inspection:** Recursively extracts and inspects Base64 candidate strings (≥ 20 characters) to neutralize obfuscated PII payloads.

### Structured Content and Tool-Call Scanner
Modern LLMs operate over multi-turn agentic workflows, embeddings, and vision inputs.
* **Multi-part message content:** Traverses supported JSON text fields in mixed content arrays. It does not inspect pixels, arbitrary encoded attachments, or every provider-specific envelope; validate preservation of non-text blocks.
* **Recursive Tool Calls & Arguments:** Deeply inspects and redacts JSON strings inside `tool_calls[*].function.arguments`.
* **JSON Recursion Bomb Defense:** Enforces a hard `max_depth = 20` traversal limit and rejects over-depth payloads with `400 Bad Request`.

## 7. ⚖️ Governance, AI Security & Compliance Tracing

The proxy decouples policy resolution from the execution plane so policy backends can be selected without embedding them in the streaming parser. Measure the latency and failure behavior of the chosen resolver in the deployment environment.

### Pluggable Tool-Call RBAC Engine (Autonomous Agent Security)
* **Implementation Mechanics:** A bounded streaming parser extracts tool execution keys (`name` or `method`) across SSE chunks. The keys are validated against a `BasePolicyResolver`; denied calls produce a rejection event. Latency and bypass resistance require deployment-specific tests.
* **Further Details:** Read the full implementation reference in [docs/pluggable-rbac-engine.md](/docs/pluggable-rbac-engine.md).

### Merkle-Attested Trace Exporter (OSCAL & OTel for SOC 2 / ISO 42001)
* **Implementation Mechanics:** RBAC and **Data Loss Prevention (DLP)** events are deterministically serialized and linked in a tamper-evident SHA-256 chain. Durable modes append signed records to local JSONL; immutable WORM retention, if required, is supplied by the deployment's evidence store. Events can also be exported as OpenTelemetry spans and NIST OSCAL machine-readable artifacts to support control assessment.
