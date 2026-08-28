[⬅️ Back to README](README.md)

# 📜 Compliance: Audit, Forensics & Legal

As enterprises rapidly operationalize Generative AI, they face an unprecedented regulatory tension: the necessity to innovate via LLMs versus the draconian legal liabilities of exposing sensitive data. Traditional cloud AI gateways fail to resolve this tension—they introduce severe streaming latency, rely on heavy memory-bound architectures, and persist data, thereby creating their own data privacy liabilities.

### The Article 12 Paradox
A critical systemic contradiction exists between the **EU AI Act's Article 12** (which mandates rigorous, immutable event logging and traceability for high-risk AI systems) and **GDPR's Article 5(1)(c) & Article 17** (which mandate strict data minimization and the right to erasure). Traditional proxy architectures cannot satisfy both; they either log too much (violating GDPR) or log too little (violating the EU AI Act).

**The Solution:** The LLM-Shield-Proxy resolves this paradox through an in-VPC mathematical sanitization layer. By combining SHA-256 sequential Merkle hash chaining (for immutable, WORM-compliant event sequencing) with RFC 6902 JSON patch differential logging (recording *what* categories were redacted, not the raw PII), the proxy achieves perfect traceability without retaining a single byte of sensitive user data.

Coupled with a Sub-millisecond SSE sliding buffer and C++ google-re2 DFA regex engine, the LLM-Shield-Proxy provides the foundation for global compliance with zero compromise on enterprise engineering performance.

---

## 🗂️ Universal Regulatory Mapping & Deep Dives

The following matrix maps the LLM-Shield-Proxy's cryptographic and systems engineering mechanics directly to major global regulatory mandates. **Click the framework name for a detailed compliance guide.**

| Regulatory Framework & Article | Specific Legal / Audit Mandate | Non-Compliance Risk & Penalty | LLM-Shield-Proxy Technical Mechanism |
| :--- | :--- | :--- | :--- |
| **[EU AI Act (Art. 12 & 14)](docs/compliance/eu_ai_act.md)** | Automated, immutable event logging & human oversight for high-risk AI systems. | Up to 7% of global annual turnover or €35M. | WORM Merkle chaining; Streaming tool-call RBAC. |
| **[GDPR (Art. 5, 17, 25, 32)](docs/compliance/gdpr.md)** | Data minimization, privacy by design, right to erasure, and state-of-the-art security. | Up to 4% of global annual turnover or €20M. | Ephemeral TTL memory eviction (<85 MB RAM); RFC 6902 differential logs; Zero-Data Mode. |
| **[HIPAA (45 CFR § 164.312)](docs/compliance/hipaa.md)** | Transmission security and access controls to safeguard ePHI in transit. | Tiered fines up to $1.5M/year per violation; Criminal penalties. | Tier-3 Quantized ONNX ClinicalBERT NER; In-Band stateless envelope cryptography. |
| **[SOC 2 Type II (CC6.1, CC6.6, CC7.2)](docs/compliance/soc2.md)** | Logical access, boundary protection, and anomaly detection/response. | Loss of enterprise contracts; reputational damage. | Pluggable streaming RBAC; Composite Agent Loop Circuit Breakers; HashiCorp Vault resolvers. |
| **[ISO/IEC 42001 & NIST SP 800-53 Rev. 5](docs/compliance/nist_iso_fips.md)** | Continuous AI risk management and systemic assessment artifact generation. | Disqualification from federal/DoD contracts; operational halting. | Universal Decision Trace Exporter generating automated NIST OSCAL compliance artifacts. |
| **[FIPS 140-3](docs/compliance/nist_iso_fips.md)** | Cryptographic module integrity and validated algorithm implementation. | Ineligibility for US Government and regulated sector deployment. | Cryptographic Known Answer Tests (KAT) for SHA-256 and AES-256-GCM. |

*(For a detailed breakdown of the internal proxy mechanics, review the [Architecture & Cryptographic Data Flow](ARCHITECTURE.md) document).*

---

## ❓ Compliance Officer & CISO FAQ

**Q1: Will our software engineering team need to rewrite existing applications?**
No. The LLM-Shield-Proxy is designed for drop-in OpenAI API compatibility. Deploying the sanitization layer requires exactly zero application code changes—engineers only need to perform a 1-line `base_url` change in their existing SDKs (e.g., pointing the OpenAI Python client to the local in-VPC proxy endpoint). The proxy handles multi-provider translation (OpenAI schemas to Anthropic, Gemini, or vLLM) transparently at the network edge.

**Q2: How do we prove zero PII egress to SOC 2 or EU regulators without logging the personal data itself?**
We utilize cryptographic WORM (Write Once, Read Many) Audit Logging via SHA-256 sequential Merkle hash chaining. The proxy generates an RFC 6902 JSON patch differential log that records the *metadata* of the redaction (e.g., "Redacted `[CREDIT_CARD]` at token offset 42") rather than the data itself. We emit a rolling SHA-256 digest over the SSE stream, producing an HMAC-signed attestation proof (Proof of Non-Egress Receipt) that guarantees to auditors no PII was transmitted to the external LLM.

**Q3: Does redacting sensitive data degrade LLM reasoning or cause streaming latency lag?**
No. To preserve LLM reasoning, we utilize a 4-Mode Pipeline. The `SYNTHETIC` mode employs canonical locale swapping to generate structurally valid synthetic data that precisely preserves BPE token counts and LLM attention weights.
For latency, the proxy utilizes a highly optimized C++ `google-re2` DFA regex engine (O(N) linear time) and a sub-millisecond sliding-window SSE lookahead buffer (<4.3 µs overhead per chunk). This allows us to rehydrate fragmented tokens across Server-Sent Events without UI stalls or noticeable lag.

**Q4: How does the proxy prevent autonomous agents from running unauthorized database or shell commands?**
By utilizing Pluggable Streaming Tool-Call RBAC. The proxy intercepts JSON-RPC 2.0 and MCP (Model Context Protocol) function calls (like `exec_sql` or `shell_exec`) mid-stream. It validates these against OPA (Open Policy Agent) and HashiCorp Vault resolvers. If an agent attempts an unauthorized action or enters a runaway state, our Composite Agent Loop Circuit Breakers atomatically halt the execution.

**Q5: How does the proxy handle AI agents talking to other AI agents or tools?**
Traditional proxies break when AI agents send complex, nested commands to each other. Our proxy features a specialized "Agent-to-Agent AI Firewall" (stateless PII synthesis and rehydration) that can instantly parse and secure machine-to-machine traffic without breaking the underlying code structure. This is the proxy's "secret sauce" for securing the next generation of autonomous AI.

**Q6: Can the proxy scan text embedded inside images or multimodal vision payloads?**
This capability is not natively supported out of the box in standard mode. Currently, our deterministic engines (google-re2, Shannon Entropy) are optimized for ultra-low latency text and SSE streams. However, domain-specific extensions or custom integrations for multimodal OCR payloads can be added on a best-effort basis upon request, or extended via BYOM (Bring Your Own Model) and BYOR (Bring Your Own Regex) configurations.

**Q7: How does our IT/DevOps department deploy and monitor this?**
The system is deployed as an Envoy gRPC `ext_proc` sidecar over Unix Domain Sockets (UDS) with strict umask policies, or as a zero-dependency Kubernetes Mutating Webhook. Monitoring is natively supported via the GRC Dispatcher, which exports OpenTelemetry `gen_ai.*` spans and NIST OSCAL assessment results directly to enterprise GRC platforms like Vanta, Drata, and Datadog. Leveraging Linux `epoll`, the proxy effortlessly supports 1,800+ concurrent streams per CPU core.
