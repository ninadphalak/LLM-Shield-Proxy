# GDPR Compliance (Articles 5, 17, 25, 32)

## Overview: Privacy by Design & Data Minimization

The General Data Protection Regulation (GDPR) mandates strict principles regarding how Personal Identifiable Information (PII) is processed. Traditional AI gateways that log raw prompts or rely on external API boundaries inherently violate GDPR's data minimization and local processing principles.

LLM-Shield-Proxy provides data-minimization, ephemeral-state, access-control, and integrity mechanisms that can support an organization's implementation of Articles 5, 17, 25, and 32. Lawful basis, notices, data-subject rights, retention, and processor/controller obligations remain organizational responsibilities.

## Article 5(1)(c) & Article 17: Data Minimization and Right to Erasure

### Ephemeral processing and TTL eviction

- **Ephemeral in-memory vaults:** The default local vault keeps reversible mappings in process memory for a configured lifetime.
- **No raw prompt in audit records:** Structured audit events record categories and decisions rather than request or response bodies.
- **TTL eviction:** Short-lived mappings expire according to configuration. This reduces retained data but is not, by itself, proof that every memory copy was cryptographically erased.

### Differential Logging
The proxy can use **RFC 6902 JSON patch differential audit logging** to record configured entity categories and actions without intentionally including matched values. Operators must verify exception, telemetry, and downstream logging paths as part of their data-minimization assessment.

## Article 25: Data Protection by Design and by Default

### In-Band AES-256-GCM Envelope Encryption
Privacy is engineered directly into the data payload before it ever leaves the VPC.
- When PII must be recoverable for the user but hidden from the external LLM, the proxy utilizes **In-Band Stateless Synthetic**.
- Detected entities are encrypted using **AES-256-GCM envelope cryptography** within the payload. The external LLM receives an encrypted cipher-token, processes the prompt, and the proxy decrypts the cipher-token upon the LLM's response.

### Text-Prompt Masking Pipeline (Human-to-LLM)
For standard text prompts, the proxy supports dynamic per-request masking via headers to maintain LLM accuracy without exposing real PII. This dictates how the proxy maps text to replacements (often utilizing an ephemeral Redis vault):
- `SYNTHETIC`: Uses canonical locale swapping to inject synthetic realistic data that preserves BPE token counts and LLM attention weights.
- `STRUCTURAL_TAG`: Replaces PII with tags like `[PERSON_1]`.
- `SCRUB`: Replaces a detected value with a static marker and does not create a rehydration mapping for that value. Other copies may still exist in source systems, process memory, logs, backups, or uninspected fields.
- `STATELESS_CRYPTO`: Encrypts selected entities in AES-256-GCM envelopes without Redis. Recovery depends on intact tokens and the correct key/context; model transformation or token loss can prevent rehydration.

### Autonomous Agent Pipeline (Machine-to-Machine)
When the proxy identifies a supported structured tool invocation, it routes the parsed payload through the AST-aware mutation path instead of applying raw string replacement. The path mutates selected values while preserving JSON serialization. Provider echo and authorized rehydration remain integration-specific and must be tested.

## Article 32: Security of Processing

The proxy provides a configurable **3-Tier Cascade Redaction Engine**:
1. **Tier 1:** Pre-compiled `google-re2` patterns for supported structured identifiers; RE2 avoids catastrophic backtracking for accepted patterns.
2. **Tier 2:** Shannon entropy heuristic to identify unstructured secret-like candidates.
3. **Tier 3:** Quantized ONNX BERT-NER executing natively in-memory (with BYOM support for XLM-RoBERTa for multilingual GDPR contexts) to extract conversational entities before egress.
4. **Structured JSON-RPC/MCP transformation:** The stateless mutation engine walks supported nested JSON values and rewrites related schemas. It preserves tested payload shapes but can reject reserved-field collisions and depends on provider/tool echo behavior. See the [Stateless Mutation Engine](/docs/features-overview).

*(Reference the [Architecture & Cryptographic Data Flow](/docs/architecture) for deeper implementation details).*
