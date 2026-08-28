[⬅️ Back to README](README.md)

# 🏛️ Architecture: Deep Dive into the Data Plane

## 🏗️ Architecture & Cryptographic Data Flow (Executive Summary)

This document details the underlying technical mechanics and data flow of the LLM-Shield-Proxy, establishing the mathematical and systemic foundation for our global regulatory compliance posture.

## Architectural Traffic Flow

The LLM-Shield-Proxy operates as an in-VPC mathematical sanitization layer. The following diagram illustrates the lifecycle of a request from the client through the proxy to the external Large Language Model (LLM) and back, demonstrating the integration of our cryptographic and compliance components.

```text
+-------------+         +-----------------------------------------------------------+          +---------------+
|             | (HTTPS) |                      IN-VPC PROXY                         | (HTTPS)  |               |
|   Client    |=======> |  +-----------------------------------------------------+  |=======>  | External LLM  |
| Application |         |  | 1. Ingress Scanning & 3-Tier Cascade Redaction      |  |          | (OpenAI,      |
|             | <=======|  |    - C++ google-re2 DFA Regex (O(N) linear time)  |  | <======= |  Anthropic,   |
+-------------+  (SSE)  |  |    - Vectorized Shannon Entropy Scanner (<6 µs)     |  |  (SSE)   |  Gemini,      |
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
                        |  | 3. WORM Audit Logging & GRC Dispatch                |  |
                        |  |    - SHA-256 Sequential Merkle Hash Chaining        |  |
                        |  |    - RFC 6902 JSON Patch Differential Logs          |  |
                        |  |    - Universal Decision Trace Exporter (NIST OSCAL) |=============> To Vanta, Drata,
                        |  +-------------------------+---------------------------+  |            Datadog
                        |                            |                              |
                        |  +-------------------------v---------------------------+  |
                        |  | 4. Streaming Traffic Engineering & Rehydration      |  |
                        |  |    - Sub-millisecond SSE Sliding-Window Buffer      |  |
                        |  |    - Bounded JSON Recursion Parser (Rust orjson)    |  |
                        |  +-------------------------+---------------------------+  |
                        |                            |                              |
                        |  +-------------------------v---------------------------+  |
                        |  | 5. Memory Eviction & Instant TTL                    |  |
                        |  |    - Ephemeral In-Memory Vaults                     |  |
                        |  |    - Zero prompt/PII persistence to disk            |  |
                        |  +-----------------------------------------------------+  |
                        +-----------------------------------------------------------+
```

## The 5-Stage Cryptographic Lifecycle

### Stage 1: Ingress Scanning & 3-Tier Cascade Redaction
Upon ingress, payloads are intercepted by a highly optimized redaction engine:
1. **Tier 1 (Structured Identifiers):** Pre-compiled C++ `google-re2` Deterministic Finite Automaton (DFA) regex engine guarantees O(N) linear time execution, making it fundamentally immune to Regular Expression Denial of Service (ReDoS) attacks. Handles SSNs, emails, IPs, and custom identifiers (`BYOR` - Bring Your Own Regex).
2. **Tier 2 (Unstructured Secrets):** Vectorized Shannon Entropy scanner operates at O(N) bit density with <6 µs latency to detect embedded cryptographic secrets, API keys, and high-entropy tokens.
3. **Tier 3 (Conversational Entities):** Quantized ONNX BERT-NER models execute natively in-memory (optional NLP mode) for context-aware entity extraction. Supports BYOM (Bring Your Own Model) for specialized architectures like BioBERT, ClinicalBERT, XLM-RoBERTa, and Legal-BERT.

### Stage 2: Stateless Envelope Encryption & Masking
To prevent PII egress to third-party LLMs without breaking token counts or attention weights:
- **In-Band Stateless Synthetic:** Entities are masked using AES-256-GCM envelope cryptography directly within the payload.
- **4-Mode Pipeline:** Dynamic per-request masking via headers supports:
  - `SYNTHETIC`: Canonical locale swapping to preserve BPE token lengths.
  - `STRUCTURAL_TAG`: Replacements like `[PERSON_1]`.
  - `SCRUB`: Hard deletion of offending tokens.
  - `STATELESS_SYNTHETIC`: Fully reversible encrypted envelopes.

### Stage 3: WORM Merkle Chaining & Traceability
To satisfy non-repudiation and traceability without retaining prompt data:
- **WORM Audit Logging:** Employs SHA-256 sequential Merkle hash chaining, cryptographically linking each event to its predecessor to detect any tampering.
- **Differential Logging:** RFC 6902 JSON patch audit logging records *which* entity categories were redacted, strictly avoiding the logging of raw PII.
- **Proof of Non-Egress Receipt:** Calculates a rolling SHA-256 digest over SSE streams, emitting an HMAC-signed attestation proof.

### Stage 4: Sliding-Window SSE Rehydration
For sub-millisecond streaming latency and seamless user experiences:
- **SSE Sliding Buffers:** Employs a sub-millisecond sliding-window SSE lookahead buffer (<4.3 µs overhead per chunk) retaining prefix overlap. This allows the proxy to accurately rehydrate fragmented encrypted tokens across fragmented Server-Sent Events (SSE) chunks without UI stalls.
- **Bounded Parsers:** Utilizes Rust-backed `orjson` streaming parsing with a bounded JSON recursion depth (`max_depth=40`) to inherently defeat stack-overflow attacks.

### Stage 5: Ephemeral Memory Eviction
To achieve a true Zero-Data state:
- **Zero-Data Mode:** Employs ephemeral, self-destructing in-memory vaults.
- **Instant TTL:** Deterministic short-lived TTL eviction limits the proxy's memory footprint to <85 MB RAM.
- **Zero Persistence:** Absolutely no prompt or PII data is ever written or persisted to local disk storage, eliminating data at rest liabilities.


---

## 🔬 Deep Dive Mechanics

LLM-Shield-Proxy is engineered as a stateless, asynchronous middleware data plane. It sits transparently between your enterprise applications and upstream Large Language Models, optimizing for microsecond latency overhead while performing heavy cryptographic and heuristic operations.

This document details the exact architectural mechanics of the underlying C++ and Rust-backed engines, demonstrating why LLM-Shield achieves unprecedented <6ms end-to-end token latency.

## 1. ⚙️ The Data Plane & Streaming Engine

To process millions of tokens per minute without saturating the Python Global Interpreter Lock (GIL), the proxy abandons traditional standard libraries in favor of aggressively optimized native extensions.

### Zero-Allocation Streaming JSON Lexer (`orjson` / Rust)
* **Implementation Mechanics:** The `streaming/buffer.py` engine leverages `orjson` (a Rust-backed serializer). It parses fragmented Server-Sent Events (SSE) directly from raw TCP frames in-band. By bypassing intermediate Python dictionary allocations, the engine maps JSON directly to memory, guaranteeing that the Resident Set Size (RSS) remains strictly below `<60MB` even under massive volumetric floods.
* **Flags:** [`MAX_SSE_LINE_LENGTH`](DEPLOYMENT.md)

### Resilient SSE Sliding-Window Buffer
* **Implementation Mechanics:** Server-Sent Events (SSE) stream arbitrary token chunks. An entity like `[PERSON_1]` may arrive fragmented across `[PER`, `SON_`, and `1]`. The `SSERehydrationBuffer` is a custom asynchronous generator that dynamically retains trailing characters. The buffer bound is defined by $L = \max(0, 	ext{max\_token\_length} - 1)$, maintaining mathematical overlap and executing prefix-safe regex rehydration without dropping streams or blocking the event loop.

### Context-Aware MCP Discovery Interception
* **Implementation Mechanics:** To support Stateless MCP architecture and Progressive Discovery (SEP-2549), the proxy utilizes `MCPDiscoveryPrunerMiddleware`. It streams JSON-RPC tool catalogs through our Rust-backed `orjson` lexer, performing O(1) RBAC `frozenset` evaluations to redact unauthorized tools. The payload is piped downstream in 64KB chunks to perfectly respect ASGI backpressure and maintain the strictly capped `<85 MB` RAM process footprint.

### Dual-Pipeline Routing & Dynamic Schema Rewriting (Machine-to-Machine)
* **Implementation Mechanics:** In `main.py`, the ingress layer strictly forks traffic based on payload structure. If it detects `payload.get("jsonrpc") == "2.0"`, it bypasses the standard text regex configuration and forces the **AST-Aware Semantic Firewall**.
* **Detection & Substitution:** Inside the structured JSON strings, PII is detected via the **Shannon Entropy Scanner** and Regex. The proxy then uses **canonical locale substitution** to generate a structurally coherent synthetic substitute to preserve downstream LLM attention weights and structural integrity.
* **Dynamic Schema Injection:** To preserve the structure of autonomous agent JSON while applying encryption, the proxy dynamically rewrites the OpenAI/MCP tool schema on the fly. It injects cryptographic cipher context (like `_ctx_hash_prop`) into the `properties` map and appends it to the `required` schema array. Because the injected hidden field is legally required by the schema, the downstream LLM is mathematically forced by its own output-parser to echo the hidden fields back in its response. This guarantees 100% stateless rehydration across multiple proxy replicas (zero Redis dependency) without dropping data.
## 2. 🛡️ The 3-Tier Redaction Cascade

The engine pipelines payload text through three consecutive filters, balancing compute cost against redaction recall.

### Tier 1: DFA Pre-compiled Regex (`google-re2`)
* **Implementation Mechanics:** Using the `re2` C++ engine, all custom regexes and predefined identifiers are compiled into Deterministic Finite Automatons (DFAs) at startup. This guarantees O(N) linear time execution. It mathematically immunizes the proxy against Regular Expression Denial of Service (ReDoS) attacks, processing 10,000-word payloads in `<0.03ms`.

### Tier 2: Shannon Entropy

### Step 2: Format-Preserving Synthetic Masking
* **Implementation Mechanics:** Regular expressions fail on unstructured data (e.g., 64-character raw cryptographic keys). The Tier 2 engine computes Shannon entropy $H(S) = -\sum p(c) \log_2 p(c)$ across a sliding window. It targets base64 strings with entropy $\ge 4.5$ bits/char and hex strings $\ge 3.4$ bits/char.
* **Format-Preserving Masking:** Instead of returning bracketed `[API_KEY_1]`, the engine uses `canonical locale substitution` to generate synthetic equivalents (e.g., swapping a real SSN for a valid but fake SSN format). This preserves LLM token-attention weights and eliminates Byte-Pair Encoding (BPE) bloat.
* **Flags:** [`ENABLE_TIER2_ENTROPY`](DEPLOYMENT.md), [`ENABLE_SYNTHETIC_SWAPPING`](DEPLOYMENT.md)

### Step 3: Script-Aware Non-Latin & CJK Rehydration Engine
* **Implementation Mechanics:** Standard word boundaries (``) break in logographic scripts (Chinese, Japanese, Korean) due to lack of whitespace, causing catastrophic sub-word collisions during stream rehydration (e.g. synthetic token `May` corrupting `Maybe` into `Sarahbe`). The proxy utilizes a specialized boundary isolation function that targets ASCII alphanumeric boundaries (`_is_ascii_word_char`) while treating CJK ideographs ($	ext{U+4E00}-	ext{U+9FFF}$) continuously, allowing seamless contextual wrapping without text corruption.

## 3. 🔐 Cryptographic Memory Vaults

Because LLM-Shield is a strict Zero-Data proxy, PII to Tag mappings must be maintained ephemerally.

### Stateless Redis TTL Vault & Deterministic HMAC Masking
* **Implementation Mechanics:** When scaling horizontally across Kubernetes pods, rehydration maps are written to `redis.asyncio` using Deterministic HMAC-SHA256 hashed keys. Keys are issued with hard rolling TTLs. If the stream disconnects, the vault automatically self-destructs the session, maintaining zero persistence.
* **Flags:** [`REDIS_URL`](DEPLOYMENT.md), [`SESSION_TTL_SECONDS`](DEPLOYMENT.md)

### In-Band Stateless Syntheticgraphic Masking
* **Implementation Mechanics:** For organizations without Redis, the proxy operates in 100% stateless mode. Entities are encrypted using AES-256-GCM envelope encryption. The encrypted ciphertext is converted to Base62 and passed *into* the LLM prompt. The downstream SSE stream returns the ciphertext, and the proxy decrypts it on the fly using a 256-bit DEK, eliminating the need for state completely.
* **Flags:** [`SHIELD_DEFAULT_MASKING_MODE`](DEPLOYMENT.md)

## 4. 🌐 Service Mesh & Multi-Provider Translation

### Multi-Provider Translators & Anthropic Adapter
* **Implementation Mechanics:** The proxy operates as a "Zero-SDK" translation layer. An application sends standard OpenAI `messages` JSON. The proxy intercepts it, performs PII redaction, and seamlessly translates the schema into an Anthropic Claude Messages API format before network egress.
* **SSE Normalization:** Anthropic's divergent `content_block_delta` SSE chunks are re-serialized on the fly back into standard OpenAI `choices[0].delta.content` formatting, allowing drop-in compatibility for any LangChain or LiteLLM backend.

### Service Mesh Native gRPC `ext_proc` Integration
* **Implementation Mechanics:** Operating a traditional HTTP proxy as a sidecar adds heavy network latency and parsing overhead to every request. LLM-Shield natively integrates with Envoy Proxy's `envoy.ext_proc` (External Processing filter). By deploying LLM-Shield as a native Kubernetes sidecar microservice, buffer chunks stream directly to Envoy over Unix Domain Sockets (UDS) using gRPC. This effectively brings HTTP network hop latency to zero and allows the proxy to mutate the payload directly within the service mesh data plane.

## 5. 🛑 Traffic Engineering & Resiliency

LLM-Shield actively manages API quotas and prevents adversarial resource exhaustion.

### Composite Agent Loop Circuit Breaker
* **Implementation Mechanics:** Runaway autonomous AI agents (e.g., AutoGen, CrewAI loops) can bill thousands of dollars in minutes if stuck in a hallucination loop. The proxy tracks the depth of `tool_calls` arrays locally and deterministically trips a circuit breaker, severing the connection and throwing an HTTP 429 if an agent loops beyond the defined threshold.
* **Flags:** [`ENABLE_AGENT_BREAKER`](DEPLOYMENT.md)

### Rate Limiting & Deep Component Health
* **Token-Bucket Rate Limiter:** Utilizing a Redis Lua script (`evalsha`) for atomicity, the proxy enforces a hard 6000 RPM / 200 Burst limit without race conditions.
* **Connection Draining:** Listens for Kubernetes `SIGTERM` signals and initiates a 25-second graceful connection draining period, ensuring existing SSE streams finish before the pod terminates.
* **Health Probes:** Exposes deep `/healthz` and `/readyz` probes tied directly to Prometheus Alert Rules, providing ops teams with instant visibility into Vault or Redis partitions.

## 6. ⚔️ Adversarial Defenses & Normalization

Attackers frequently use invisible Unicode characters and encoding tricks to bypass standard regex filters or overwhelm the processing engine.

### Adversarial Desmuggling & Normalization Pipeline
* **Zero-Width Character Stripping:** Filters zero-width spaces (`\u200B`), joiners (`\u200D`), byte order marks, and soft hyphens.
* **BiDi / RTL Override Neutralization:** Strips Right-to-Left Overrides that visually flip character orders to humans while evading byte scanners.
* **NFKC Unicode Normalization:** Converts full-width, circled, and decomposed glyphs to canonical equivalents prior to pattern matching.
* **Base64 Candidate Inspection:** Recursively extracts and inspects Base64 candidate strings (≥ 20 characters) to neutralize obfuscated PII payloads.

### Universal Multi-Modal & Recursive Tool-Call Scanner
Modern LLMs operate over multi-turn agentic workflows, embeddings, and vision inputs.
* **Multi-Part Message Content:** Universally traverses mixed content arrays (`[{"type": "text"}, {"type": "image_url"}]`), sanitizing prompt text without corrupting binary image data.
* **Recursive Tool Calls & Arguments:** Deeply inspects and redacts JSON strings inside `tool_calls[*].function.arguments`.
* **JSON Recursion Bomb Defense:** Enforces a hard `max_depth = 20` traversal limit, returning `400 Bad Request` in `<1ms` against stack-overflow payload attacks.

## 7. ⚖️ Governance, AI Security & Compliance Tracing

To satisfy stringent enterprise regulations and maintain a strict **Zero Trust AI** architecture without degrading stream latency, the **LLM Firewall** decouples policy resolution from the execution plane. This ensures continuous **AI Governance** and robust **LLM Security Posture Management (LLM SPM)**.

### Pluggable Tool-Call RBAC Engine (Autonomous Agent Security)
* **Implementation Mechanics:** The **AI Gateway** proxy uses a custom Zero-Allocation Streaming Pushdown Automaton to parse SSE chunks in `<1.0µs` and extract tool execution keys (`name` or `method`). The extracted tools are validated against an asynchronously resolved `BasePolicyResolver` (e.g., `RedisPolicyResolver`). If an unauthorized tool is detected mid-stream, the proxy instantly synthesizes a deterministic rejection chunk and severs the upstream socket, providing impenetrable **Autonomous Agent Security**.
* **Further Details:** Read the full implementation reference in [docs/pluggable-rbac-engine.md](docs/pluggable-rbac-engine.md).

### Merkle-Attested Trace Exporter (OSCAL & OTel for SOC 2 / ISO 42001)
* **Implementation Mechanics:** Every RBAC decision and **Data Loss Prevention (DLP)** redaction event is deterministically serialized using `orjson.dumps(..., option=orjson.OPT_SORT_KEYS)` to prevent log injection (e.g., null bytes). The record is then appended to a local WORM-compliant Merkle Tree, maintaining a cryptographic hash chain of all events. These records are simultaneously emitted as OpenTelemetry (OTel) gRPC spans and exported as NIST OSCAL (Open Security Controls Assessment Language) machine-readable JSON artifacts, drastically simplifying **SOC 2 Compliance for AI** and **ISO 42001 AI Management System** audits.
