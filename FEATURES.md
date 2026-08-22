# LLM-Shield-Proxy Feature Catalog

## Section A: Data Protection & PII Redaction
* **Tier 1 Pre-Compiled Regex Engine**: Leverages `google-re2` for high-performance, pre-compiled regular expressions that guarantee $O(N)$ execution time and complete immunity against ReDoS attacks.
* **Tier 2 Shannon Entropy Scanner**: Automatically detects and flags unstructured cryptographic secrets and high-entropy API keys by evaluating the Shannon entropy of incoming token streams.
* **Tier 3 Quantized ONNX BERT-NER**: Employs a strictly quantized ONNX BERT-NER model executing natively in-memory to provide high-accuracy contextual free-text extraction with sub-millisecond latency.
* **v3 Stateless AST-Aware Semantic PII Firewall**: Inspects and safely mutates structured tool invocations using an AST-aware semantic firewall designed for MCP and Agent-to-Agent JSON-RPC 2.0 payloads.
* **Format-Preserving Synthetic Masking & Entropy**: Replaces sensitive tokens with mathematically and structurally coherent Faker substitutes to preserve downstream LLM attention weights and syntax.
* **In-Band Stateless Cryptographic Masking**: Secures data in transit using zero-data AES-256-GCM envelope encryption directly within the payload.
* **Stateless Redis TTL Vault & Deterministic HMAC Masking**: Employs ephemeral Redis TTL vaults with deterministic HMAC masking to support highly flexible anonymization modes and zero-data liability.
* **Granular Entity Policy Scopes**: Provides robust department-level security profiles utilizing $O(1)$ in-memory tenant profile mapping via Virtual Keys enforcing a FAIL_CLOSED Zero-Trust default.

## Section B: Ultra-Low Latency Streaming & Traffic Engineering
* **Sub-Millisecond SSE Sliding-Window Buffer**: Processes fragmented stream chunks securely in real-time by reconstructing overlapping tokens without breaking Server-Sent Events (SSE) streaming connections.
* **Zero-Allocation Streaming JSON Lexer**: Utilizes a highly optimized, Rust-backed `orjson` parser to evaluate continuous data streams while maintaining a zero-allocation profile and an incredibly lean <55MB RAM footprint.
* **Multi-Provider Translators**: Democratizes multi-cloud routing through zero-SDK OpenAI-to-Anthropic request transformation and dynamic SSE stream normalization.
* **Anthropic Adapter Implementation**: Provides seamless schema translations for Anthropic models directly at the network edge without requiring client-side SDK modifications.
* **Pluggable Tool-Call RBAC (MCP Governance)**: Intercepts autonomous JSON-RPC tool executions and enforces strict logical access controls against your existing Redis, OPA, or HashiCorp Vault infrastructure.

## Section C: Advanced Threat Defense & Enterprise Resilience
* **Cryptographic Canary Prompt Tripwires**: Defends against aggressive extraction attempts by planting verifiable inbound honeytokens and enforcing immediate outbound Generator Exit socket drops upon triggered violations.
* **Entity-Weighted Blast Radius Limits**: Prevents dangerous bulk data exfiltration events by implementing horizontally scalable Redis Token-Bucket circuit breakers.
* **LLM FinOps Chargeback Meter**: Facilitates strict multi-tenant chargebacks and resource accounting by streaming asynchronous Prometheus metrics that track token consumption down to the individual identity.
* **Provider Failover Routing**: Guarantees zero-downtime service continuity via explicit header-driven rerouting to secondary provider mirrors without subjecting clients to unapproved model downgrades.
* **Antifragile Exponential Retries**: Swiftly recovers from upstream instability by implementing native asyncio jitter and exponential backoffs to elegantly absorb severe network timeouts and 429/50x errors.
* **Composite Agent Loop Circuit Breaker**: Automatically halts runaway AutoGen and CrewAI autonomous loops by dynamically tracking array depths and recursive tool call patterns.
* **Traffic Engineering & Resiliency**: Hardens infrastructure via Redis evalsha Token-Bucket Rate Limiters (6000 RPM/200 Burst), Kubernetes 25s SIGTERM connection draining, and robust upstream key overriding.

## Section D: Enterprise Auditing & Compliance
* **WORM-Compliant Merkle Attestation & Audit Logging**: Emits structured compliance events containing timestamps, tenant IDs, redacted entity types, and session metadata to provide mathematical proof of non-egress.
* **Cryptographic SHA-256 Hash Chaining**: Guarantees tamper-evidence for strict SOC 2 and HIPAA audits by ensuring every log entry is cryptographically signed and chained to the previous record's hash.
* **Cryptographic Proof of Non-Egress Merkle Attestation**: Generates mathematical proof of zero-egress payloads to satisfy stringent legal and forensic compliance requirements.
* **Universal Decision Trace Exporter**: Enriches enterprise observability platforms by emitting highly structured NIST OSCAL decision artifacts intertwined comprehensively with OpenTelemetry spans.
* **Zero-Overhead OpenTelemetry (OTel) Tracing**: Ensures full observability without latency penalties by handling W3C traceparent distributed tracing propagation via a dedicated asynchronous background thread.
* **Kubernetes-Native GRC Dispatcher**: Seamlessly dispatches non-blocking webhooks directly into risk platforms like Vanta, Drata, and Sprinto using a highly robust, zero-dependency Kubernetes architecture.
* **Dynamic Canary Watermarking & Steganography**: Tracks provenance for internal leak forensics by injecting invisible, verifiable cryptographic watermarks into outbound text streams.
* **FIPS 140-3 KAT & RFC 6902 Differential Audit Logging**: Satisfies stringent federal requirements through strict compliance logging formats, cryptographic self-tests, and RFC 6902 differential patching.

## Section E: Secure Infrastructure & Service Mesh
* **Service Mesh Native gRPC ext_proc Integration**: Eliminates redundant HTTP network hops by streaming buffers directly over Unix Domain Sockets for immediate Envoy sidecar compatibility.
* **Centralized Enterprise Secrets & mTLS**: Secures backend communications via native HashiCorp Vault integrations (AppRole / K8s / Token) combined with a non-blocking TTL cache and X.509 mTLS transport.
* **Zero-Dependency Kubernetes Mutating Webhook**: Enables frictionless drop-in K8s integration by seamlessly injecting the LLM-Shield sidecar container and mTLS certificates without requiring external controllers.
* **Deep Component Health Probes and Prometheus Alert Rules**: Provides granular native endpoints for K8s liveness and readiness probes covering Redis connectivity and Vault mTLS states, alongside pre-packaged alert rules.
* **Role-Based Policy-as-Code & Hot-Reloading**: Automates zero-downtime file polling for live policy updates with an $O(1)$ in-memory flattening architecture that maps incoming `virtual_key_id` requests to distinct security roles.
* **Universal Dynamic Override Engine**: Enables $O(1)$ hash lookups via `contextvars.ContextVar`, thread-safe off-loop Executor propagation using `copy_context().run()`, and the ability to override any global `.env` configuration per-tenant without function-signature bloat.
