import re

with open("README.md", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Edit Title, Subtitle, and add Mermaid Diagram
title_pattern = r"# LLM-Shield-Proxy 🛡️.*?\[!\[Build Status"
new_title = """# LLM-Shield-Proxy 🛡️

<img src="docs/LLM-Shield-Proxy-paper-v2.gif" width="600" alt="LLM-Shield-Proxy Demo" />

**Ultra-Low Latency Generative AI Sanitization for Highly Regulated Enterprise Infrastructure**

LLM-Shield-Proxy is a hyper-fast, FastAPI-based streaming gateway designed specifically for environments where data privacy is paramount (Banking, Healthcare, Legal). It intercepts and sanitizes real-time LLM streams to prevent the leakage of Non-Public Personal Information (NPI), Protected Health Information (PHI), and Payment Card Industry (PCI) data without degrading the end-user streaming experience.

By utilizing a highly optimized **Tiered Detection Approach**, LLM-Shield-Proxy applies guardrails at the microsecond level, keeping your AI applications compliant with strict InfoSec mandates (GLBA, PCI-DSS, HIPAA) while maintaining zero-perceived-latency.

```mermaid
flowchart LR
    A[Direct LLM Stream] -->|Risk of Data Leak| B((Security Blocked))
    C[Legacy Gateways] -->|+150ms Latency| D((UX Destroyed))
    E[LLM-Shield-Proxy] -->|Sub-10µs Token Overlay| F((Secure & Fast))
    
    style B fill:#fef2f2,stroke:#ef4444
    style D fill:#fef2f2,stroke:#ef4444
    style F fill:#f0fdf4,stroke:#22c55e
```

[![Build Status"""
text = re.sub(title_pattern, new_title, text, flags=re.DOTALL)

# 2. Reduce BYOM and BYOR
byom_pattern = r"### 🧠 Bring Your Own Model.*?---"
new_byom_byor = """### 🔌 Pluggable Extensibility (BYOM & BYOR)
LLM-Shield-Proxy is highly extensible without risking latency or ReDoS.
* **[Bring Your Own Model (BYOM)](docs/features/data-protection-pii-redaction/tier-3-quantized-onnx-bert-ner.md):** Plug in any domain-specific Hugging Face transformer exported to ONNX (e.g., ClinicalBERT for HIPAA, FinBERT for Finance) for contextual Tier 3 extraction.
* **[Bring Your Own Regex (BYOR)](docs/features/data-protection-pii-redaction/bring-your-own-regex-byor-custom-rules.md):** Inject custom C++ compiled DFA regex patterns for internal proprietary tokens via `custom_regex.yaml`. Mathematically guaranteed O(N) execution for ReDoS immunity.

---"""
text = re.sub(byom_pattern, new_byom_byor, text, flags=re.DOTALL)

# 3. Trim 9-point architectural deep-dives
arch_pattern = r"## 🧠 Core Architecture & Technical Innovations.*?---"
new_arch = """## 🧠 Core Architecture & Technical Innovations

LLM-Shield-Proxy delivers enterprise privacy and zero-trust security through highly optimized architectural breakthroughs. 

> **[View the Complete Architecture Deep Dive 🏛️](ARCHITECTURE.md)**: For an exhaustive breakdown of the streaming lexer, memory mechanics, and service mesh integrations.

### [1. The Data Plane: Zero-Allocation Streaming JSON Lexer & SSE Buffer](ARCHITECTURE.md#1-️-the-data-plane--streaming-engine)
Rust-backed `orjson` engine parses fragmented Server-Sent Events with mathematical overlap bounding, enabling high-throughput without Python GIL saturation and capping memory at `<85 MB`.

### [2. O(N) DFA Pre-compiled Regex Engine (`google-re2`)](ARCHITECTURE.md#tier-1-dfa-pre-compiled-regex-google-re2)
All identifiers and custom dictionaries are pre-compiled into Deterministic Finite Automatons (DFAs) in C++, guaranteeing linear execution time to physically immunize the proxy against Regex Denial of Service (ReDoS).

### [3. Dual-Mode Shannon Entropy Secret Scanner](ARCHITECTURE.md#tier-2-shannon-entropy--format-preserving-synthetic-masking)
Vectorized O(N) math loop evaluating H(S) bit density to instantly intercept unstructured 64-char cryptographic keys in `<6 µs`.

### [4. Stateless Cryptographic Rehydration (JSON-RPC)](ARCHITECTURE.md#3--cryptographic-memory-vaults)
Dynamically intercepts OpenAI/MCP tool schemas on the fly, injecting cryptographic hidden fields (like `_ctx_hash_prop`) into the JSON Schema `required` array. This mathematically forces the LLM to echo back the reversible cipher, enabling infinite horizontal scalability without any Redis dependency.

---"""
text = re.sub(arch_pattern, new_arch, text, flags=re.DOTALL)

# 4. Modify Auditor Evidence Mapping
auditor_pattern = r"If you are deploying LLM-Shield to satisfy a compliance audit, map the proxy's features directly to your Trust Services Criteria. See our complete \[Auditor Evidence Mapping\]\(COMPLIANCE.md#.*?\. Includes documentation for the \*\*Universal Decision Trace Exporter\*\* and \*\*Kubernetes-Native GRC Dispatcher\*\*\."
new_auditor = r"If you are deploying LLM-Shield to satisfy a compliance audit, map the proxy's features directly to your Trust Services Criteria. See our complete [Auditor Evidence Mapping](COMPLIANCE.md)."
text = re.sub(auditor_pattern, new_auditor, text, flags=re.DOTALL)

# 5. Modify Enterprise Security & Threat Defenses test count
test_pattern = r"LLM-Shield-Proxy is validated against an exhaustive suite of \*\*176 automated unit, integration, and adversarial fuzzing tests\*\*\."
new_test = r"LLM-Shield-Proxy is validated against an exhaustive suite of **automated unit, integration, and adversarial fuzzing tests**."
text = re.sub(test_pattern, new_test, text, flags=re.DOTALL)

# 6. Global Faker replace
text = text.replace("Faker", "canonical locale")

with open("README.md", "w", encoding="utf-8") as f:
    f.write(text)
