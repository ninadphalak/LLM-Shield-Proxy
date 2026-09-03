[⬅️ Back to README](/)

# Architecture and Data Flow

This document details the technical data flow of the LLM-Shield-Proxy.

## Request Path

The proxy operates inside an operator-controlled network (e.g., a VPC). It acts as a transparent reverse proxy between client applications and external LLM providers.

```text
+-------------+         +-----------------------------------------------------------+          +---------------+
|             | (HTTPS) |                      IN-VPC PROXY                         | (HTTPS)  |               |
|   Client    |=======> |  +-----------------------------------------------------+  |=======>  | External LLM  |
| Application |         |  | 1. Ingress Scanning & 3-Tier Cascade Redaction      |  |          | (OpenAI,      |
|             | <=======|  |    - C++ google-re2 DFA Regex                       |  | <======= |  Anthropic,   |
+-------------+  (SSE)  |  |    - Local Shannon Entropy Scanner                  |  |  (SSE)   |  Gemini,      |
                        |  |    - Quantized ONNX BERT-NER (in-memory)            |  |          |  vLLM)        |
                        |  +-------------------------+---------------------------+  |          +---------------+
                        |                            |                              |
                        |  +-------------------------v---------------------------+  |
                        |  | 2. Configured Masking                               |  |
                        |  |    - SYNTHETIC, STRUCTURAL, SCRUB, STATELESS_CRYPTO |  |
                        |  +-------------------------+---------------------------+  |
                        |                            |                              |
                        |  +-------------------------v---------------------------+  |
                        |  | 3. Audit Metadata and Export                        |  |
                        |  |    - SHA-256 Predecessor Hash Chaining              |=============> To Datadog/Splunk
                        |  |    - Ed25519 Signatures                             |  |
                        |  +-------------------------+---------------------------+  |
                        |                            |                              |
                        |  +-------------------------v---------------------------+  |
                        |  | 4. Streaming Traffic Engineering & Rehydration      |  |
                        |  |    - Bounded SSE Sliding-Window Buffer              |  |
                        |  |    - Bounded JSON Recursion Parser                  |  |
                        |  +-----------------------------------------------------+  |
                        +-----------------------------------------------------------+
```

## Five Processing Stages

### Stage 1: Ingress Scanning
The proxy applies enabled detectors sequentially to outbound text payloads:
1. **Tier 1:** Pre-compiled `google-re2` regular expressions for structured identifiers.
2. **Tier 2:** Shannon entropy calculation for detecting unstructured secrets.
3. **Tier 3:** (Optional) Local ONNX BERT-NER model for conversational entity extraction.

### Stage 2: Masking
Detected PII is replaced before egress to the LLM. Available modes:
- **SYNTHETIC:** Generates fake, structurally similar data (e.g., a fake SSN).
- **STRUCTURAL_TAG:** Inserts a clear tag (e.g., `[EMAIL_1]`).
- **SCRUB:** Deletes the text entirely.
- **STATELESS_CRYPTO:** Replaces the text with AES-256-GCM ciphertext.

### Stage 3: Tamper-Evident Auditing
The proxy generates an audit trail for security events:
- Events are linked via SHA-256 hashes to detect deletion or reordering.
- Events are signed via Ed25519 to verify origin authenticity.
- This layer logs security decisions (e.g., blocked tools) but does not record the raw PII itself.

### Stage 4: Streaming Rehydration
When the LLM streams the response back, the proxy must undo the masking.
- **Sliding-Window Buffer:** Rehydrates placeholders even if they are split across multiple Server-Sent Event (SSE) chunks.
- **Parser Bounds:** Protects against JSON recursion bombs (`max_depth = 20`) to prevent Denial of Service.

### Stage 5: Ephemeral Memory Eviction
For stateful masking (SYNTHETIC, STRUCTURAL), the proxy must remember the original text to rehydrate the response.
- State is kept in memory or Redis.
- Mappings expire automatically based on a configured TTL to ensure sensitive data is not retained indefinitely. 
- Stateless crypto masking bypasses this requirement entirely.

## Input Normalization
To prevent LLMs or attackers from bypassing detection using alternate text encodings:
* **Zero-Width Stripping:** Removes invisible characters (`\u200B`, `\u200D`).
* **BiDi Neutralization:** Strips Right-to-Left Overrides used for visual obfuscation.
* **Unicode Normalization:** Converts full-width and composed glyphs into canonical forms before scanning.
