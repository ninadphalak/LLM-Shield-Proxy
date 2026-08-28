readme_content = """# LLM-Shield-Proxy 🛡️

<img src="docs/LLM-Shield-Proxy-paper-v2.gif" width="600" alt="LLM-Shield-Proxy Demo" />

**Ultra-Low Latency Generative AI Sanitization for Financial Infrastructure**

LLM-Shield-Proxy is a hyper-fast, FastAPI-based streaming gateway designed specifically for highly regulated environments (Banking, FinTech, Healthcare). It intercepts and sanitizes real-time LLM streams to prevent the leakage of Non-Public Personal Information (NPI) and Payment Card Industry (PCI) data without degrading the end-user streaming experience.

By utilizing a highly optimized **Tiered Detection Approach**, LLM-Shield-Proxy applies guardrails at the microsecond level, keeping your AI applications compliant with strict InfoSec mandates (GLBA, PCI-DSS, HIPAA) while maintaining zero-perceived-latency.

[![Build Status](https://github.com/ninadphalak/LLM-Shield-Proxy/actions/workflows/ci.yml/badge.svg)](https://github.com/ninadphalak/LLM-Shield-Proxy/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/llm-shield-proxy.svg?color=green)](https://pypi.org/project/llm-shield-proxy/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![License: CC BY 4.0](https://img.shields.io/badge/License-CC_BY_4.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Docker Pulls](https://img.shields.io/badge/docker-ready-blue.svg)](https://hub.docker.com/)

---

## 🏦 The Enterprise Bottleneck
Financial institutions and healthcare providers are rushing to deploy Generative AI, but face a critical infrastructure roadblock:
1. **Compliance Risk:** Direct LLM streams can leak sensitive customer data (SSNs, Routing Numbers, PANs).
2. **The Latency Trade-off:** Traditional API gateways and enterprise Data Loss Prevention (DLP) proxies add 100ms+ of latency per request. For token-by-token LLM streaming, this destroys the UX.

**LLM-Shield-Proxy solves this.** It is a lightweight, drop-in middleware that redacts sensitive data in transit with a measured token overhead of just **~5 microseconds**.

```mermaid
flowchart LR
    A[Direct LLM Stream] -->|Risk of NPI Leak| B((Security Blocked))
    C[Legacy Gateways] -->|+150ms Latency| D((UX Destroyed))
    E[LLM-Shield-Proxy] -->|Sub-10µs Token Overlay| F((Secure & Fast))
    
    style B fill:#fef2f2,stroke:#ef4444
    style D fill:#fef2f2,stroke:#ef4444
    style F fill:#f0fdf4,stroke:#22c55e
```

## ⚙️ Core Features for Regulated Environments

* **Microsecond Token Overhead:** Built on FastAPI's `StreamingResponse` to process tokens faster than network jitter.
* **Tiered Detection Architecture:** Dynamically scale your sanitization strictness for any Data Protection Impact Assessment (DPIA):
  * *Tier 1 (Regex/Pattern):* Sub-10µs redaction of standard financial PII (Credit Cards, SSNs, Account Numbers).
  * *Tier 2 (Heuristics):* Lightweight logic for complex cryptographic secrets (API Keys, Hex tokens).
  * *Tier 3 (Semantic):* Context-aware ONNX BERT-NER evaluation for sophisticated contextual entities (e.g., Medical Diagnoses, Organization Names).
* **Zero Long-Term Storage (Zero-Data Mode):** Self-destructing TTL session vault built for zero data liability. No prompts or context windows are ever written to persistent disk.
* **Universal Decision Trace Exporter:** Every PII redaction decision is cryptographically sealed for WORM-compliant Merkle Tree logging.

> [!NOTE]
> See the complete list of [Enterprise Flagship Features](FEATURES.md) including BYOM, BYOR, and Pluggable RBAC.

## ⚡ Verifiable Benchmarks

Performance in financial infrastructure must be quantified. We strictly isolate and measure Time to First Token (TTFT) and Inter-Token Latency overhead.

| Gateway Setup | TTFT Overhead | Inter-Token Delay | Best For |
|---------------|---------------|-------------------|----------|
| Direct Stream (Baseline)| 0.00 ms | 0.00 ms | Unregulated PoCs |
| **LLM-Shield-Proxy (Tier 1)** | **+ 0.05 ms** | **+ 5 µs** | **Strict NPI/PCI Compliance** |
| Legacy Enterprise Gateway | + 45.00 ms | + 2.5 ms | Non-streaming APIs |

*We encourage infrastructure teams to verify these claims locally using the included benchmark suite.*

---

## 🚀 5-Minute Local Audit

Evaluate the proxy's latency and redaction locally without exposing corporate network traffic.

```bash
# 1. Spin up the proxy container in background
docker compose up -d

# 2. Send a test stream containing dummy PII
curl -X POST http://localhost:8000/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer shield-virtual-key" \\
  -d '{"messages": [{"role": "user", "content": "My routing number is 021000021."}], "stream": true}'
```

*(The stream will return with the account number seamlessly redacted in real-time).*

---

## Why Not Microsoft Presidio / Legacy Proxies?

It's a crowded space. Here is exactly why you should deploy LLM-Shield-Proxy instead of the alternatives:

* **Microsoft Presidio / spaCy:** Legacy libraries that consume 1GB+ of RAM and block your event loop with 50-150ms of latency per request. LLM-Shield-Proxy uses a flat `<85 MB` footprint with `<6 µs` latency overhead.
* **Cloud AI Safety APIs (Azure/AWS):** Checking for PII by sending raw data out of your VPC defeats the purpose. With LLM-Shield-Proxy, the data never leaves your infrastructure unredacted.
* **Standard Regex Gateways:** They break on asynchronous Server-Sent Events (SSE). If a sensitive token is split across two streaming packets, standard gateways let it leak. LLM-Shield-Proxy uses a sliding-window lookahead buffer to seamlessly hold split tokens without breaking stream formatting.

LLM-Shield-Proxy is **not** a model router (like LiteLLM or LangChain). It works *alongside* them. Put LLM-Shield-Proxy directly in front of your orchestrator to guarantee deterministic data masking before routing.

### 🤝 The Orchestrators (What we complement)
LLM-Shield-Proxy is **not** a model router. It is designed to deploy as a transparent edge proxy directly in front of industry-standard orchestration tools. It stacks with your existing AI routing infrastructure, requires zero code changes, and is compatible out-of-the-box with:

* **Orchestration Frameworks:** LangChain, LlamaIndex, Semantic Kernel, AutoGen, CrewAI.
* **AI Gateways & Routers:** LiteLLM, Cloudflare AI Gateway, Kong AI Gateway, Portkey. *(Note: You can seamlessly stack LLM-Shield-Proxy in front of LiteLLM to combine multi-model routing with strict zero-egress PII redaction and AES-256-GCM encryption).*
* **Local & Open-Source Inference:** vLLM, Ollama, NVIDIA NIM, Hugging Face TGI.
* **Upstream Providers:** OpenAI, Anthropic, Google Gemini, DeepSeek, Mistral.

---

## 🛡️ Dual-Pipeline Redaction Modes

LLM-Shield-Proxy intelligently routes traffic through one of two specialized pipelines based on the payload structure. Read the [Architecture Document](ARCHITECTURE.md) for full details on the sliding-window buffer and Vault integrations.

```mermaid
flowchart TD
    Client[Client / Agent] -->|Payload| Proxy{Is JSON-RPC?}
    
    Proxy -->|Yes: jsonrpc 2.0| Schema[Dynamic Schema Rewriter]
    Schema -->|Inject 'required' crypto fields| AST[AST-Aware Firewall]
    AST -->|Apply STATELESS_CRYPTO| LLM[Cloud LLM]
    
    Proxy -->|No: Standard Text| Text{Masking Mode?}
    Text -->|SYNTHETIC| Redis[(Redis Vault)]
    Text -->|STATELESS_CRYPTO| InBand[AES-256-GCM]
    Redis --> LLM
    InBand --> LLM
```

---

## 🏢 Enterprise Hardware Sizing Guide

Based on extreme stress testing, the Proxy scales highly efficiently across multi-core architectures. The proxy engine is fully asynchronous and achieves its highest throughput on Linux environments utilizing `epoll`.

### Production Sizing (Enterprise Linux)
*   **Rule of Thumb:** Provision 1 CPU core for every **1,800** expected peak concurrent users.
*   **Mid-Tier (16 Cores)**: ~28,800 Concurrent Users. *(Recommended: AWS c6i.4xlarge, GCP c2-standard-16, or Azure Standard_F16s_v2)*
*   **High-Tier (32 Cores)**: ~57,600 Concurrent Users. *(Recommended: AWS c6i.8xlarge, GCP c2-standard-32, or Azure Standard_F32s_v2)*
*   **Memory (RAM) Footprint:** The proxy is strictly **CPU-bound**. With a lightweight Resident Set Size (RSS) of `<85 MB` per worker, memory-optimized instances are completely unnecessary. Standard compute-optimized instances provide vastly more RAM than the proxy will ever consume.

> [!NOTE]
> **Windows Deployment Note (`SO_REUSEPORT`):** While the proxy runs efficiently on Windows, scaling to extreme high-concurrency with multiple workers is constrained by the Windows TCP stack. Windows does not natively support the `SO_REUSEPORT` socket option. Under massive load, this can result in less efficient connection routing across Uvicorn workers. For maximum enterprise production scale, Linux deployments are generally recommended. *In rigorous load tests, a single Python core on Windows tops out around ~800 to 900 concurrent streaming users before encountering `accept()` backlog saturation (`ConnectionRefusedError`).*

---

## 🌍 Open Source Roadmap & Contributions

I am committed to maintaining LLM-Shield-Proxy as the fastest ultra-low latency redaction engine for LLMs. I am actively looking for open-source contributors and collaborators to help execute the following technical roadmap. If you submit a PR, I will personally review and merge your architecture contributions:

1. **Cythonize the Sliding-Window Buffer:** Compile the pure-Python async generator (`streaming.py`) into a C-extension binary to aggressively drive down tail latencies for high-throughput enterprise deployments.
2. **Upstream Integration:** Track upstream discussions and context for resolving SSE stream fragmentation in enterprise sandboxes, such as the [NVIDIA/OpenShell #2763](https://github.com/NVIDIA/OpenShell/issues/2763) proposal.

If you want to contribute to enterprise AI security, check out [CONTRIBUTING.md](CONTRIBUTING.md) and claim an issue (e.g., [Help Cythonize the proxy! #15](https://github.com/ninadphalak/LLM-Shield-Proxy/issues/15))!

---

## 🏢 Enterprise Support & Community

If your organization is evaluating, benchmarking, or deploying LLM-Shield-Proxy to unblock LLM streaming and meet strict compliance requirements (like SOC 2/HIPAA), I encourage you to engage with the community:

* **Architecture Discussions:** Open a GitHub Discussion to share your feedback on high-throughput deployments, custom proxy pipelines, or benchmark results.
* **Enterprise Case Studies:** If your startup or enterprise is using the proxy in production, let me know! I highlight production architectures and feature enterprise teams in my community benchmarks.
* **Bug Reports & Features:** Submit technical issues or feature requests via the GitHub Issue tracker.

LLM-Shield-Proxy is actively gathering feedback from CISOs, DevOps engineers, and Cybersecurity professionals to shape the open-source compliance roadmap.

---

## 📄 Intellectual Property & Licensing

**LLM-Shield-Proxy** is an original engineering work authored and maintained by **Ninad Phalak**. 

* **Open-Source License:** The core engine, proxy middleware, and streaming buffers are licensed under the **Apache 2.0 License** (see [LICENSE](LICENSE) for details).
* **Patent Status:** Core architectural mechanisms are protected under **U.S. Patent Pending** status:
  * **App. No. 64/126,730**: Protects the asynchronous Server-Sent Event (SSE) sliding-window lookahead buffer and the memory-bounded two-tier inference routing cascade.
  * **App. No. 64/139,263**: Protects the stateless cryptographic JSON-RPC/MCP AST masking, HKDF subkey encryption, and generative AI metadata schema coercion.

---

## Citation

If you reference this architecture, benchmark methodology, or sliding-window buffer implementation, please cite:

Phalak, N. (2026). Quantifying Latency and Token Overhead in Real-Time LLM Stream Sanitization: A Tiered Detection Approach (Version 1.0.0). Zenodo. https://doi.org/10.5281/zenodo.21955770

```bibtex
@misc{phalak2026quantifying,
  author       = {Phalak, Ninad},
  title        = {Quantifying Latency and Token Overhead in Real-Time LLM Stream Sanitization: A Tiered Detection Approach},
  month        = aug,
  year         = 2026,
  publisher    = {Zenodo},
  doi          = {10.5281/zenodo.21955770},
  url          = {https://doi.org/10.5281/zenodo.21955770}
}
```

---

## 📖 Complete Documentation

* **[Features Catalog](FEATURES.md)**
* **[Deployment & Configuration](DEPLOYMENT.md)**
* **[Architecture & Math](ARCHITECTURE.md)**
* **[Policy-as-Code (RBAC)](POLICIES.md)**
* **[Security Model](SECURITY.md)**
"""

with open("README.md", "w", encoding="utf-8") as f:
    f.write(readme_content)
