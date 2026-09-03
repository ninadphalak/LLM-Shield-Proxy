# LLM-Shield-Proxy Feature Catalog

This catalog outlines the available features and their stability tiers.

* `Supported`: CI exercises the feature end-to-end against target infrastructure.
* `Beta`: Tests cover the code, but depend heavily on operator configuration or mock external systems.
* `Experimental`: Target infrastructure is not fully exercised, or implements a partial protocol.
* `Research`: Exploratory, no support commitment, may be removed without warning.

## Section A: Data Protection & PII Redaction
* `Supported` **Tier 1 Pre-Compiled Regex**: High-performance DFA regex scanning via `google-re2`.
* `Supported` **Tier 2 Shannon Entropy Scanner**: Detects unstructured secrets (e.g., base64 keys) based on entropy density.
* `Supported` **Tier 3 Quantized ONNX BERT-NER**: Local NLP named-entity recognition. CI runs a checksum-pinned quantized DistilBERT model through real `onnxruntime` inference. The current engine supplies only `input_ids` and `attention_mask`; a model that also requires `token_type_ids` produces no name spans and logs a warning. It also labels every non-`O` prediction as `PERSON` instead of reading `id2label`.
* `Supported` **Format-Preserving Synthetic Masking**: Generates realistic synthetic replacements for detected PII.
* `Beta` **In-Band Stateless Cryptographic Masking**: AES-256-GCM encryption of detected PII, eliminating stateful mapping databases.
* `Supported` **Redis TTL Vault**: Stores original-to-masked token mappings in Redis for stateful rehydration.
* `Beta` **4-Mode Per-Request Masking**: Override masking modes (`SYNTHETIC`, `SCRUB`, etc.) per-request via HTTP headers.
* `Beta` **Bring-Your-Own-Regex (BYOR)**: Add custom detection rules via a YAML configuration file.
* `Supported` **JSON Recursion Bomb Protection**: Blocks overly nested JSON payloads to prevent DoS attacks.

## Section B: Streaming and Traffic Engineering
* `Supported` **SSE Sliding-Window Buffer**: Safely reconstructs PII split across streaming Server-Sent Event (SSE) chunks.
* `Supported` **Bounded Streaming JSON Lexer**: Incrementally parses JSON without unbounded memory allocation.
* `Experimental` **Anthropic Adapter**: Translates OpenAI-formatted requests into Anthropic API calls.
* `Supported` **HTTP/2 Connection Pooling**: Multiplexes upstream connections for reduced latency.

## Section C: Traffic Controls and Resilience
* `Beta` **Provider Failover Routing**: Automatically routes traffic to secondary provider URLs on failure.
* `Beta` **Agent Loop Circuit Breaker**: Detects and breaks infinite AI agent loops (returns HTTP 429).
* `Beta` **Token Bucket Rate Limiting**: Redis-backed request rate limiting per virtual key.
* `Supported` **Graceful Pod Draining**: Completes in-flight SSE streams before shutting down on `SIGTERM`.

## Section D: Audit and Evidence
* `Supported` **Hash-Chained Audit Logging**: Emits tamper-evident, SHA-256 chained audit logs.
* `Supported` **Ed25519 Audit Signatures**: Cryptographically signs audit events.
* `Beta` **Asynchronous OpenTelemetry (OTel)**: Exports traces via W3C standard OTLP.
* `Supported` **OSCAL 1.2 Export**: Generates NIST-compatible compliance assessment artifacts.
* `Supported` **Compliance-Pack CLI**: Verifies hash chains and signatures offline.

## Section E: Infrastructure and Service Mesh
* `Experimental` **gRPC ext_proc Integration**: Native Envoy proxy integration over Unix Domain Sockets.
* `Experimental` **Vault Secrets and mTLS**: Retrieves configuration secrets and handles Mutual TLS via HashiCorp Vault.
* `Experimental` **Kubernetes Mutating Webhook**: Automatically injects the proxy container as a sidecar into Kubernetes pods.
* `Beta` **Role-Based Policy-as-Code Hot-Reloading**: Reloads RBAC and tool policies from YAML without restarting the proxy.
* `Supported` **SSRF & DNS-Rebinding Egress Firewall**: Blocks malicious internal tool calls by resolving and verifying all DNS records against a CIDR denylist.
