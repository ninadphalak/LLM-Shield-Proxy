# GDPR Compliance (Articles 5, 17, 25, 32)

## Overview: Privacy by Design & Data Minimization

The General Data Protection Regulation (GDPR) mandates strict principles regarding how Personal Identifiable Information (PII) is processed. Traditional AI gateways that log raw prompts or rely on external API boundaries inherently violate GDPR's data minimization and local processing principles.

The LLM-Shield-Proxy resolves this through a mathematically rigorous "Zero-Data" architecture, ensuring compliance with Articles 5, 17, 25, and 32.

## Article 5(1)(c) & Article 17: Data Minimization and Right to Erasure

### Zero-Data Mode & Ephemeral TTL Eviction
To guarantee data minimization and inherently satisfy the Right to Erasure, the proxy retains no state.
- **Ephemeral In-Memory Vaults:** All data processing occurs in ephemeral, self-destructing in-memory vaults.
- **Zero Persistence:** There is zero prompt or PII persistence to disk. By never writing PII to persistent storage, the system avoids data-at-rest liabilities entirely.
- **Deterministic TTL Eviction:** Memory is aggressively managed with short-lived TTL (Time-To-Live) eviction, maintaining an ultra-lean <85 MB RAM footprint. Once the SSE stream terminates, the memory is cryptographically zeroed.

### Differential Logging
Instead of logging raw PII for audit purposes (which violates minimization), the proxy utilizes **RFC 6902 JSON patch differential audit logging**. It records only the *categories* of data redacted (e.g., `[REMOVED_EMAIL]`) and the systemic action taken, never the underlying personal data.

## Article 25: Data Protection by Design and by Default

### In-Band AES-256-GCM Envelope Encryption
Privacy is engineered directly into the data payload before it ever leaves the VPC.
- When PII must be recoverable for the user but hidden from the external LLM, the proxy utilizes **In-Band Stateless Synthetic**.
- Detected entities are encrypted using **AES-256-GCM envelope cryptography** within the payload. The external LLM receives an encrypted cipher-token, processes the prompt, and the proxy decrypts the cipher-token upon the LLM's response.

### Text-Prompt Masking Pipeline (Human-to-LLM)
For standard text prompts, the proxy supports dynamic per-request masking via headers to maintain LLM accuracy without exposing real PII. This dictates how the proxy maps text to replacements (often utilizing an ephemeral Redis vault):
- `SYNTHETIC`: Uses canonical locale swapping to inject synthetic realistic data that preserves BPE token counts and LLM attention weights.
- `STRUCTURAL_TAG`: Replaces PII with tags like `[PERSON_1]`.
- `SCRUB`: Executes a permanent hard deletion. On the outbound request to the LLM, the PII is replaced with a static `[REDACTED]` marker. On the inbound response back to the user, because the original data was completely destroyed, it cannot be rehydrated.
- `STATELESS_CRYPTO`: Encrypts entities using fully reversible AES-256-GCM envelopes, allowing the downstream system to recover the PII while hiding it from the LLM (no Redis required).

### Autonomous Agent Pipeline (Machine-to-Machine)
When the proxy detects structured AI tool invocations (like `jsonrpc: 2.0`), it completely bypasses the Text-Prompt pipeline. Standard masking (like `SYNTHETIC`) corrupts JSON code. Therefore, for Machine-to-Machine traffic, the proxy **always** strictly enforces the AST-Aware Semantic Firewall. It parses the syntax tree and applies `STATELESS_SYNTHETIC` directly to the JSON values, guaranteeing no structural breakage and zero Redis dependency.

## Article 32: Security of Processing

The proxy ensures state-of-the-art security via a **3-Tier Cascade Redaction Engine**:
1. **Tier 1:** Pre-compiled C++ `google-re2` DFA regex engine (O(N) linear time, ReDoS-immune) for structured identifiers.
2. **Tier 2:** Vectorized Shannon Entropy scanner (O(N) bit density, <6 µs) to detect unstructured cryptographic secrets.
3. **Tier 3:** Quantized ONNX BERT-NER executing natively in-memory (with BYOM support for XLM-RoBERTa for multilingual GDPR contexts) to extract conversational entities before egress.
4. **Tier 4 (Agent-to-Agent AI Firewall):** When autonomous AI agents talk to each other using complex code formats (like JSON-RPC or MCP), standard proxies break. The proxy acts as a firewall for this machine-to-machine traffic, instantly identifying and hiding nested PII without breaking the underlying code structure. (For technical details on this "Stateless PII Synthesis & Rehydration", see the [Stateless Mutation Engine](../../FEATURES.md) in the features catalog).

*(Reference the [Architecture & Cryptographic Data Flow](../../ARCHITECTURE.md) for deeper implementation details).*
