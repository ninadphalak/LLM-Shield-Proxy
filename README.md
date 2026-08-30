# LLM-Shield-Proxy 🛡️

[![Build Status](https://github.com/ninadphalak/LLM-Shield-Proxy/actions/workflows/ci.yml/badge.svg)](https://github.com/ninadphalak/LLM-Shield-Proxy/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/llm-shield-proxy.svg?color=green)](https://pypi.org/project/llm-shield-proxy/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![License: CC BY 4.0](https://img.shields.io/badge/License-CC_BY_4.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Docker Pulls](https://img.shields.io/badge/docker-ready-blue.svg)](https://hub.docker.com/)
[![Docs & Live Demo](https://img.shields.io/badge/docs-interactive%20demo-00ff9d)](https://project-0039f5fd-ac66-4a1c-9e0.web.app)

> 📖 **[Read the full docs and try the interactive PII-redaction playground →](https://project-0039f5fd-ac66-4a1c-9e0.web.app)**
> Type real-looking PII into your browser and watch it get redacted before it ever reaches an LLM, then rehydrated on the way back - no signup, no server calls, entirely client-side.

<img src="website/docs/LLM-Shield-Proxy-paper-v2.gif" width="600" alt="LLM-Shield-Proxy Demo" />

**Independently verifiable, in-VPC streaming privacy with bounded processing and audit evidence**

LLM-Shield-Proxy is a hyper-fast, FastAPI-based streaming gateway designed specifically for environments where data privacy is paramount (Banking, Healthcare, Legal). It intercepts and sanitizes real-time LLM streams to prevent the leakage of Non-Public Personal Information (NPI), Protected Health Information (PHI), and Payment Card Industry (PCI) data without degrading the end-user streaming experience.

The project uses a **Tiered Detection Approach** and publishes the tests needed to measure its safety and performance in a specific environment. It can support technical controls used in GLBA, PCI-DSS, HIPAA, and SOC 2 programs; installing it does not make an organization compliant.

> **Apache 2.0:** every source line is inspectable, and the proxy is completely free to download and self-host.

In this project, **zero egress** has a narrow, testable meaning: *unredacted protected data does not reach the configured upstream boundary*. It does not mean the process makes no network calls; the proxy necessarily sends the transformed request to the upstream selected by its operator.

**Option 1: Standard Egress**
<br>
<a target="_blank" href="website/docs/assets/diagram-standard.svg?v=3">
  <img src="website/docs/assets/diagram-standard.svg?v=3" alt="Standard Egress Diagram" style="max-width: 100%; height: auto;" />
</a>

**Option 2: Zero-Internet Air-Gapped Mode**
<br>
<a target="_blank" href="website/docs/assets/diagram-airgapped.svg?v=3">
  <img src="website/docs/assets/diagram-airgapped.svg?v=3" alt="Air-Gapped Egress Diagram" style="max-width: 100%; height: auto;" />
</a>
<br>
*\* Egress Gateway can be any standard network proxy (e.g., Squid, Envoy, LLMLite, NGINX).*

> **Technical controls supporting SOC 2 Type II and HIPAA safeguards for LLM streams, without breaking real-time latency.**

**LLM-Shield-Proxy** is an open-source streaming privacy gateway and LLM firewall deployed inside an operator-controlled environment. It transforms OpenAI-compatible requests before the configured upstream boundary and rehydrates authorized values in incremental SSE responses.

Designed to enforce **Zero Trust AI** and support enterprise privacy compliance programs (**SOC 2 trust criteria**, HIPAA, HITRUST technical safeguards) without breaking real-time streaming latency.

## 🛡️ Dual-Pipeline Redaction Architecture

LLM-Shield-Proxy intelligently routes traffic through two distinct redaction pipelines based on the payload structure. This ensures that autonomous agents don't crash from broken syntax trees, while human prompts get the highest quality contextual masking.

<br>
<a target="_blank" href="website/docs/assets/diagram-dual-pipeline.svg?v=2">
  <img src="website/docs/assets/diagram-dual-pipeline.svg?v=2" alt="Dual-Pipeline Redaction Architecture" style="max-width: 100%; height: auto;" />
</a>

### A. Human-to-LLM (Text Prompts / STATELESS_CRYPTO)
For standard conversational text, the proxy respects your configured masking mode. You can choose from four strategies:
1. **SYNTHETIC (Stateful):** (e.g. replacing *'My SSN is 000-00-0000'* with *'My SSN is 111-11-1111'*). Swaps PII with canonical locale fakes (e.g., `John` -> `Maya`). Preserves LLM attention weights and token counts. Requires Redis.
2. **STRUCTURAL_TAG (Stateful):** (e.g. replacing *'My SSN is 000-00-0000'* with *'My SSN is [SSN_1]'*). Swaps PII with explicit bracketed tags (e.g., `[PERSON_1]`). Requires Redis.
3. **SCRUB (Stateless):** (e.g. replacing *'My SSN is 000-00-0000'* with *'My SSN is ***'*). Destructive one-way redaction (`***`). Cannot be rehydrated.
4. **STATELESS_CRYPTO (Zero Storage):** (e.g. replacing *'My SSN is 000-00-0000'* with *'My SSN is [enc_3x9kL]'*). Encrypts PII in-band via AES-256-GCM. Zero Redis dependency.

### B. Machine-to-Machine (JSON-RPC / Tool Calls)
When the proxy detects structured AI tool calls or JSON-RPC `2.0` payloads, it **bypasses your configuration** and strictly enforces an **AST-Aware Semantic Firewall** with **STATELESS_SYNTHETIC**.
* **Why?** Blindly running regex over raw JSON strings can corrupt syntax (e.g., matching a JSON key or injecting unescaped characters), causing agent crashes.
* **The Solution:** The proxy parses the payload into an Abstract Syntax Tree (AST), replaces selected leaf values, and can bundle reversible AES-256-GCM context (for example, `{"_shield_val": "Maya", "_shield_ctx": "aesgcm..."}`). AST mutation preserves JSON syntax; provider echo and rehydration behavior must be integration-tested.

---

### 🔥 Enterprise Flagship Features
* **[Fragment-Safe SSE Rehydration](#-how-it-works-the-data-flow):** The sliding-window buffer reconstructs protected placeholders split across Server-Sent Event chunks. Run the reproducible conformance harness below for environment-scoped measurements.
* **[Zero-Egress Synthetic Masking](#️-dual-pipeline-redaction-architecture):** Advanced **Data Loss Prevention (DLP) for LLMs** using format-preserving substitution (Regex + Shannon Entropy + ONNX NER) ensuring PII never traverses the public internet.
* **[Air-Gapped Egress Gateway Mode](docs/features/air-gapped-egress.md):** Allows operation in strict Zero-Internet corporate subnets by securely routing all upstream traffic through an internal egress gateway, optionally stripping auth headers for internal mTLS architectures.
* **[Zero-Data Stateless Syntheticgraphy](#4-in-band-stateless-crypto--ephemeral-vaults):** Ephemeral TTL vaults and AES-256-GCM envelope encryption avoid persistent prompt storage in the masking path. Measure process memory in your selected installation mode and workload.
* **[Role-Based Policy-as-Code & Hot-Reloading](POLICIES.md):** Zero-downtime YAML file watcher (`policies.yaml`) dynamically maps `virtual_key_id` identities to granular security roles, custom PII profiles, and thread-safe $O(1)$ setting overrides.
* **[Universal Decision Trace Exporter](#-enterprise-compliance-audit-forensics--legal):** PII redaction and agent RBAC decisions can be emitted as hash-chained, signed evidence plus **NIST OSCAL artifacts** and **OpenTelemetry `gen_ai.*` spans**. True WORM retention requires a separately configured immutable sink.
* **[Ed25519-Signed Audit Receipts & Evidence Packs](website/docs/features/enterprise-auditing-compliance/ed25519-signed-audit-receipts.md):** Audit events are signed for offline verification (public key at `GET /api/v1/audit/pubkey`); `llm-shield-proxy compliance-report --framework=hipaa` bundles the verification summary, OSCAL results, and a SHA-256 manifest. These artifacts support an audit; they do not certify compliance.
* **[Streaming Tool-Call Interception & Agent Governance](docs/PLUGGABLE_RBAC_ENGINE.md):** Intercepts real-time LLM function calls (e.g., `exec_sql`, `shell_exec`) with a bounded JSON parser, enforcing fail-closed tool access controls backed by Redis, OPA, or Vault policy stores to prevent agent drift.
* **[Context-Aware Tool Catalog Pruner (MCP Discovery)](docs/features/ultra-low-latency-streaming-traffic-engineering/context-aware-mcp-discovery-pruner.md):** Intercepts JSON-RPC discovery payloads and evaluates tool names against resolved policy before the catalog reaches the model. Redis caching is optional; latency is deployment-specific.
* **[Service Mesh Native gRPC Sidecar](#5-service-mesh-native-grpc-ext_proc--k8s-sidecar):** Stream buffers directly over Unix Domain Sockets (UDS) via Envoy's `ext_proc` for zero HTTP network hops, paired with a zero-dependency Kubernetes Mutating Webhook.
* **[Bounded Regex Engine](#️-bring-your-own-regex-byor-enterprise-rule-injection):** Supported patterns can use `google-re2` to avoid catastrophic backtracking; validate fallback behavior and unsupported constructs at startup.
* **[Universal Zero-SDK Translators](#-the-drop-in-proof-zero-sdk-integration):** Drop-in compatibility for existing OpenAI SDKs with automatic edge-translation to Anthropic, Gemini, and vLLM schemas.
* **[Edge-Level Agent Identity Enforcer](docs/features/agent_identity_enforcer.md):** Validates configured workload identity and DPoP proofs before governed tool calls. Measure latency and exercise replay/failure cases in the pilot profile.



## ⚡ 60-Second Quickstart & Deployment

### 🔄 The Drop-In Proof: Zero-SDK Integration
Because LLM-Shield-Proxy natively mimics the OpenAI specification, **you do not need to rewrite your application code**. You simply change the `base_url` in your SDK or the endpoint in your `curl` command. The proxy intercepts the payload, redacts it, and translates the schema to the correct upstream provider automatically.

**Option A: cURL**
```bash
# ❌ Before: Sending raw PHI directly to OpenAI
curl https://api.openai.com/v1/chat/completions \
  -H "Authorization: Bearer sk-openai-key" \
  -d '{"messages": [{"role": "user", "content": "My SSN is 000-00-0000"}]}'

# ✅ After: Sending payload through LLM-Shield (Zero Egress)
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer shield-virtual-key" \
  -d '{"messages": [{"role": "user", "content": "My SSN is 000-00-0000"}]}'
```

**Option B: Python SDK (1-Line Change)**
```python
from openai import OpenAI

client = OpenAI(
    api_key="your-openai-api-key",
    base_url="http://localhost:8000/v1",  # Point to LLM-Shield-Proxy
)

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Contact Sarah Connor at sarah@example.com or 555-0199."}],
    stream=True,
)

for chunk in response:
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

### 🚀 Docker Quickstart
Spin up the zero-egress proxy in seconds.

**Option A: Run the Live Streaming Demo (Docker Compose)**
```bash
# 1. Spin up the proxy container in background
docker compose up -d

# 2. Verify health probe
curl http://localhost:8000/healthz

# 3. Run the live demo script
python examples/demo.py
```

**Option B: Standalone Container (Production Base)**
```bash
docker run -d -p 8000:8000 \
  -e OPENAI_API_KEY="sk-your-openai-api-key" \
  -e HOST="0.0.0.0" \
  -e PORT=8000 \
  --name llm-shield-proxy \
  ghcr.io/ninadphalak/llm-shield-proxy:latest
```

---

### 📦 Installation Options & Configuration Strategy

For architectural diagrams showing VPC and Air-Gapped Egress gateway setups, please refer to the **[Deployment Topologies](docs/features/deployment-topologies.md)** guide.

LLM-Shield-Proxy is heavily modular. You can configure the engine based on your specific compliance ROI and memory constraints:

| Installation Tier | Command | Capabilities Included | Use Case / Trade-off |
| :--- | :--- | :--- | :--- |
| **Standard Mode** | `pip install llm-shield-proxy` | **Tier 1 (Regex)** & **Tier 2 (Shannon Entropy)** | Best for structured identifiers and secret candidates. It does not provide population-level recall guarantees and can miss conversational names or novel formats. Measure RSS and latency on your workload. |
| **Full NLP Mode**<br>*(Contextual NER)* | `pip install "llm-shield-proxy[ner]"` | Adds **Tier 3 (ONNX Runtime NER)** | Adds a configurable local ONNX model for contextual entities. Quality and memory depend on the selected model and corpus; publish model, dataset, splits, and confidence intervals with any accuracy claim. |

> **Enabling Tier 3 ONNX NER:** When installed with `[ner]`, enable deep neural entity extraction by setting `ENABLE_TIER3_ONNX_NER=true` in your `.env` or environment variables (and optionally point `ONNX_MODEL_PATH` to custom model weights). If disabled or not installed, the engine automatically and gracefully bypasses Tier 3 with zero startup overhead.

### 🔌 Pluggable Extensibility (BYOM & BYOR)
LLM-Shield-Proxy is highly extensible without risking latency or ReDoS.
* **[Bring Your Own Model (BYOM)](docs/features/data-protection-pii-redaction/tier-3-quantized-onnx-bert-ner.md):** Plug in any domain-specific Hugging Face transformer exported to ONNX (e.g., ClinicalBERT for HIPAA, FinBERT for Finance) for contextual Tier 3 extraction.
* **[Bring Your Own Regex (BYOR)](docs/features/data-protection-pii-redaction/bring-your-own-regex-byor-custom-rules.md):** Inject custom C++ compiled DFA regex patterns for internal proprietary tokens via `custom_regex.yaml`. Mathematically guaranteed O(N) execution for ReDoS immunity.

---

## 💥 The Problem vs. The LLM-Shield-Proxy Solution

| Existing Legacy Proxies | LLM-Shield-Proxy |
| :--- | :--- |
| **Destroys Real-Time SSE Streaming:** Buffers entire responses before scanning, causing multi-second UI latency stalls. | **Ultra-Low Latency Streaming:** Redacts and re-hydrates delta-by-delta as SSE packets stream. |
| **Optional heavyweight NLP dependencies:** Some deployments use large NLP runtimes. | **Tiered local processing:** Standard mode avoids a neural runtime; optional ONNX mode has a workload- and model-dependent footprint. |
| **Data Liability:** Stores user PII in long-term databases. | **Zero Long-Term Storage (Zero-Data Mode):** Self-destructing TTL session vault built for zero data liability. Operates in strict "Zero-Data Mode"-no prompts, PII, or context windows are ever written to persistent disk or external storage. |
| **Inspection API egress:** Some products send source data to a separate inspection service. | **Testable upstream boundary:** scanning occurs in the operator's deployment and the conformance test checks that known raw protected values are absent from the serialized configured-upstream request. |

### 🏛️ Built for Trust & Transparency
Designed specifically for highly regulated enterprise environments, strict **Zero Trust AI** network architectures, and security-first engineering teams implementing **LLM Security Posture Management (LLM SPM)**.
1. **Keeps inspection local:** Deploy the shield inside your boundary and test that unredacted protected data does not reach the configured upstream.
2. **Ephemeral masking state:** Sensitive prompt mappings use in-memory or TTL-backed vaults unless the operator selects an external state store.
3. **Measurable stability:** Run the conformance and load-test protocols in the exact installation mode, host, concurrency, audit, and upstream configuration you plan to operate.
4. **Transparent Rule Engine:** Combines transparent, deterministic pattern matching with Shannon entropy and local ONNX neural entity recognition.

---

## Why Not <s style="color: gray;">Microsoft Presidio</s> <sup>*any other proxy?*</sup>

It's a crowded space. Here is exactly why you should deploy LLM-Shield-Proxy instead of the alternatives:

* **Microsoft Presidio / spaCy:** Different detector stacks make different quality, dependency, and resource trade-offs. Compare them on the same corpus and service-level protocol; the project does not currently publish a validated universal memory or total-proxy latency advantage.
* **Cloud AI Safety APIs (Azure/AWS):** Checking for PII by sending raw data out of your VPC defeats the purpose. With LLM-Shield-Proxy, the data never leaves your infrastructure unredacted.
* **Standard Regex Gateways:** They break on asynchronous Server-Sent Events (SSE). If a sensitive token is split across two streaming packets, standard gateways let it leak. LLM-Shield-Proxy uses a sliding-window lookahead buffer to seamlessly hold split tokens without breaking stream formatting.
* **LiteLLM / LangChain:** LLM-Shield-Proxy is not a model router or orchestration framework. Put it in front of the orchestrator and verify the serialized configured-upstream boundary using the conformance suite.

### 🤝 The Orchestrators (What we complement)
LLM-Shield-Proxy is **not** a model router. It is designed to deploy as a transparent edge proxy directly in front of industry-standard orchestration tools. It stacks with your existing AI routing infrastructure, requires zero code changes, and is compatible out-of-the-box with:

* **Orchestration Frameworks:** LangChain, LlamaIndex, Semantic Kernel, AutoGen, CrewAI.
* **AI Gateways & Routers:** LiteLLM, Cloudflare AI Gateway, Kong AI Gateway, Portkey. *(Note: You can seamlessly stack LLM-Shield-Proxy in front of LiteLLM to combine multi-model routing with strict zero-egress PII redaction and AES-256-GCM encryption).*
* **Local & Open-Source Inference:** vLLM, Ollama, NVIDIA NIM, Hugging Face TGI.
* **Upstream Providers:** OpenAI, Anthropic, Google Gemini, DeepSeek, Mistral.

Place **LLM-Shield-Proxy** in front of them to apply configured masking and produce evidence that can support SOC 2 control testing before the payload reaches the selected upstream.




---


### How It Works (The Data Flow)

#### 📥 Inbound (Prompt Sanitization)
1. **Intercept:** Your client routes a standard OpenAI / LangChain request through `localhost:8000`.
2. **Dual-Pipeline Routing:** The proxy checks the payload type. Standard text goes to the **3-Tier Cascade Engine** (Regex -> Entropy -> ONNX NER). JSON-RPC tool calls are routed to the **AST-Aware Firewall**.
3. **Secure Substitution:** Sensitive data is swapped out using your configured mode (Synthetic Fakes, Structural Tags, or AES-GCM). Stateful mappings are stored in the local Redis vault; stateless mappings are encrypted in-band.
4. **Clean Egress:** The sanitized payload is forwarded to the LLM. Your raw PII never traverses the public internet.

#### 📤 Outbound (Streaming Rehydration)
1. **SSE Stream Intercept:** The LLM streams the sanitized response back via Server-Sent Events (SSE).
2. **Prefix-Aware Buffer:** Because LLMs often fragment tokens across SSE chunks, our patent-pending sliding-window buffer retains trailing prefix overlap (e.g., `[PER`... `SON_1]`) ensuring split tokens never leak.
3. **Incremental Rehydration:** When a synthetic name or tag is fully assembled, the proxy retrieves the original data (via Redis or AES decryption) and resumes valid SSE delivery. Measure end-to-end overhead with the published protocol; component microbenchmarks are not total proxy latency.

---

## 🧠 Core Architecture & Technical Innovations

LLM-Shield-Proxy delivers enterprise privacy and zero-trust security through highly optimized architectural breakthroughs.

> **[View the Complete Architecture Deep Dive 🏛️](ARCHITECTURE.md)**: For an exhaustive breakdown of the streaming lexer, memory mechanics, and service mesh integrations.

### [1. The Data Plane: Bounded Streaming JSON Lexer & SSE Buffer](ARCHITECTURE.md#1-️-the-data-plane--streaming-engine)
`orjson` parses fragmented Server-Sent Events while the overlap buffer retains a bounded suffix. The conformance report measures retained-buffer bounds and process allocation separately; it does not assert a universal process-RSS ceiling.

### [2. O(N) DFA Pre-compiled Regex Engine (`google-re2`)](ARCHITECTURE.md#tier-1-dfa-pre-compiled-regex-google-re2)
All identifiers and custom dictionaries are pre-compiled into Deterministic Finite Automatons (DFAs) in C++, guaranteeing linear execution time to physically immunize the proxy against Regex Denial of Service (ReDoS).

### [3. Dual-Mode Shannon Entropy Secret Scanner](ARCHITECTURE.md#tier-2-shannon-entropy--format-preserving-synthetic-masking)
An O(N) Shannon-entropy operation identifies high-density secret candidates. Its current behavior should be measured with the published benchmark harness.

### [4. Stateless Syntheticgraphic Rehydration (JSON-RPC)](ARCHITECTURE.md#3--cryptographic-memory-vaults)
Dynamically intercepts OpenAI/MCP tool schemas and injects cryptographic context fields into the JSON Schema `required` array. Provider echo behavior is not guaranteed by schema alone and must be tested with the selected model and parser.

---

## 🛡️ Enterprise Security & Threat Defenses

LLM-Shield-Proxy is validated against an exhaustive, continuously growing suite of **170+ automated unit, integration, and adversarial fuzzing tests**.

Below is a high-level summary of our defense architecture. For the complete **22-vector Threat Matrix**, detailed implementation specifications, and vulnerability coverage, view our [Deep Dive Security & Threat Model Documentation](SECURITY.md).

| Security Domain | Defense Mechanisms & Capabilities |
| :--- | :--- |
| **🛡️ Core Cryptographic Masking & Defenses** | 1. [Data Loss Prevention (DLP) for LLMs (Synthetic Masking & Entropy)](SECURITY.md#data-loss-prevention-dlp-for-llms-synthetic-masking--entropy)<br>2. [In-Band Stateless Syntheticgraphic Masking](SECURITY.md#in-band-stateless-cryptographic-masking)<br>3. [Stateless Redis TTL Vault & Deterministic HMAC Masking](SECURITY.md#stateless-redis-ttl-vault--deterministic-hmac-masking)<br>4. [Dynamic Canary Watermarking & Steganography (Leak Forensics)](SECURITY.md#dynamic-canary-watermarking--steganography-leak-forensics) |
| **🛑 Threat Prevention & Isolation** | 1. [Autonomous Agent Security (Composite Agent Loop Circuit Breaker)](SECURITY.md#autonomous-agent-security-composite-agent-loop-circuit-breaker)<br>2. [Granular Entity Policy Scopes & Zero Trust AI Defaults (O(1) mapping)](SECURITY.md#granular-entity-policy-scopes--zero-trust-ai-defaults)<br>3. [Bounded Streaming JSON Lexer](SECURITY.md#zero-allocation-streaming-json-lexer)<br>4. [Cryptographic Canary Prompt Tripwires](SECURITY.md#cryptographic-canary-prompt-tripwires)<br>5. [Entity-Weighted Blast Radius Limits](SECURITY.md#entity-weighted-blast-radius-limits) |
| **📜 Audit, Forensics, and Compliance** | 1. [Tamper-Evident Audit Logging & SHA-256 Hash Chaining](SECURITY.md#worm-compliant-merkle-attestation--audit-logging)<br>2. [Cryptographic SHA-256 Hash Chaining](SECURITY.md#cryptographic-sha-256-hash-chaining)<br>3. [Cryptographic Proof of Non-Egress Cryptographic Attestation](SECURITY.md#cryptographic-proof-of-non-egress-merkle-attestation)<br>4. [FIPS 140-3 KAT & RFC 6902 Differential Audit Logging](SECURITY.md#fips-140-3-kat--rfc-6902-differential-audit-logging) |
| **🏗️ Secure Infrastructure & Service Mesh** | 1. [Centralized Enterprise Secrets & mTLS](SECURITY.md#centralized-enterprise-secrets--mtls)<br>2. [Service Mesh Native gRPC ext_proc Integration](SECURITY.md#service-mesh-native-grpc-ext_proc-integration)<br>3. [Zero-Dependency Kubernetes Mutating Webhook](SECURITY.md#zero-dependency-kubernetes-mutating-webhook)<br>4. [Traffic Engineering & Resiliency](SECURITY.md#traffic-engineering--resiliency)<br>5. [Provider Failover Routing & Exponential Retries](ARCHITECTURE.md#provider-failover-routing) |
| **🔄 Multi-Provider Adapters** | 1. [Multi-Provider Translators & Anthropic Adapter](SECURITY.md#multi-provider-translators--anthropic-adapter) |

## 📜 Enterprise Compliance: Audit, Forensics & Legal

LLM-Shield-Proxy is engineered specifically to help enterprises adopt Generative AI while supporting data privacy regulations like HIPAA and SOC 2 audit requirements. These are technical controls that map to specific framework requirements - deploying this proxy is one control among many a full compliance program requires, not a certification or a substitute for legal/compliance review.

Below is a summary of our compliance mappings. For the exhaustive deep-dive mapping, view our [Enterprise Compliance Documentation](COMPLIANCE.md).

### 🛡️ SOC 2 & ISO 42001 Auditor Evidence Mapping
If you are deploying LLM-Shield to satisfy a compliance audit, map the proxy's features directly to your Trust Services Criteria. See our complete [Auditor Evidence Mapping](COMPLIANCE.md).

| Compliance Domain | Supported Features & Capabilities |
| :--- | :--- |
| **🏥 HIPAA Transmission Security** | Local O(1) Redaction, Tier-2 Shannon Entropy + canonical locale synthetic substituting. No raw PHI traverses public internet to third-party APIs. |
| **🛡️ SOC 2 Audit Evidence** | SHA-256 hash chaining and Ed25519 signatures emit tamper-evident structured records; durable and immutable retention remain deployment choices. |
| **⚖️ Legal & Egress Provenance** | Cryptographic Proof of Non-Egress Cryptographic Attestation. Dynamic Canary Watermarking for insider leak forensics. |
| **🔐 Data Integrity & Storage** | Zero long-term storage. In-Band Stateless AES-256-GCM masking or ephemeral Redis TTL Vault mapping with Deterministic HMAC masking. |
---


---

## 🏢 Enterprise Capacity Planning

Capacity is deployment-specific. Worker count, detector tier, payload distribution, SSE duration, TLS, audit durability, upstream behavior, and host kernel all affect the result. No universal concurrency or process-RSS limit is currently claimed.

### Production sizing protocol

1. Run the exact container, detector tier, audit mode, and upstream adapter planned for production.
2. Sweep concurrency while recording completed streams, errors, event-loop lag, CPU, peak RSS, and p50/p95/p99 end-to-end overhead.
3. Repeat at least five independent trials, publish raw results and environment metadata, and size from the lower confidence bound with operational headroom.

## ⚡ Performance & Latency Benchmarks

The repository includes two complementary benchmark paths. Run the conformance harness for deterministic safety checks and local microbenchmark distributions:

```bash
llm-shield-proxy benchmark --iterations 2000 --json-out CONFORMANCE_LATEST.json
```

The current public artifact is a 10,000-iteration pre-release self-test on Windows 11, CPython 3.14.7, AMD64:

| In-process operation | p50 | p95 | p99 |
| :--- | ---: | ---: | ---: |
| Empty-vault SSE buffer | `26.9 µs` | `53.1 µs` | `73.5 µs` |
| Protected-token SSE buffer | `41.4 µs` | `74.0 µs` | `102.7 µs` |

The report also verifies all seven conformance domains, a retained prefix of 5 characters against an 8-character bound, and a 4,656-byte peak for the declared Python-allocation scope. That allocation figure is not process RSS.

These measurements exclude ASGI, HTTP/TLS, network, upstream-model, concurrency, and durable-audit costs. See the [result, environment, checksum, and limitations](website/docs/conformance/results.md).

### ⚡ Under the Hood: Architectural Speed Optimizations
The implementation uses component-level optimizations whose service-level effect must be measured:

1. **O(N) Shannon Entropy:** Tier 2 evaluates raw unformatted secret candidates using a frequency `Counter`.
2. **DFA Pre-compiled Regex Caching:** Tier 1 identifiers are compiled into deterministic finite automatons (DFA) at startup, ensuring constant-time structural matching.
3. **Native JSON Parsing:** The asynchronous SSE rehydration buffer uses `orjson`; comparative speedups require a published baseline, payload corpus, and environment.
4. **Lazy-Loaded ONNX Neural Pipeline:** The Tier 3 Named Entity Recognition (NER) pipeline is strictly lazy-loaded. If disabled, it gracefully bypasses neural inference with zero startup overhead or memory bloat.
5. **Bounded Recursion (JSON Bomb Defense):** Traversal of nested payloads and `tool_calls` is hard-capped at `max_depth = 40` to bound adversarial nesting.
6. **Connection Pooling & Caching:** The proxy reuses upstream connections and selected computations; neither mechanism implies zero routing overhead.

### 🚀 High-Concurrency & Enterprise Load Capacity
The proxy uses an asynchronous event loop and connection pooling. Publish service-level capacity only after running the controlled load protocol for the exact deployment.

To run the conformance, component benchmark, and stress test suites locally:

```bash
# Streaming privacy conformance and in-process distributions
llm-shield-proxy benchmark --iterations 2000 --json-out CONFORMANCE_LATEST.json

# Locust concurrent stream stress suite
locust -f benchmarks/load_test.py --headless -u 500 -r 50 --run-time 10m --host http://localhost:8000
```

---

## ⚖️ Engineering Philosophy & Architecture Trade-offs

Building a low-overhead streaming proxy requires bounded algorithms and reproducible measurement:

1. **Custom SSE Sliding-Window:** The async buffer retains prefix overlap (`L = max_token_length - 1`). The conformance suite exercises adversarial fragmentation, including UTF-8 code points split across transport chunks.
2. **ONNX Runtime:** Optional quantized models avoid requiring a full training framework in the serving process. Actual accuracy, startup time, and RSS are model- and environment-specific.
3. **`orjson`:** Native deserialization is used on the streaming path. Comparative claims require the same workload and baseline.
4. **Information Theory:** Structural matching is complemented by an O(N) Shannon-entropy heuristic. It is not a complete secret detector and must be evaluated on positive and hard-negative corpora.

---

## 📝 Known Limitations

Please be aware of the following current limitations:
- **Text-sized Base64 only:** The detector inspects encoded candidates up to 8,192 characters. For larger bodies it skips the encoded interior while retaining 256-character boundary guards for adjacent plaintext. It does not scan text embedded in images or arbitrary encoded attachments (for example, OpenAI Vision payloads).
- **Supported Languages:** Multilingual support requires providing your own ONNX model via BYOM (`ONNX_MODEL_PATH`). By default, the proxy falls back to an English-optimized NLP model.
- **Non-Standard Streaming:** Designed for standard Server-Sent Events (SSE). Custom or proprietary streaming protocols may bypass the sliding-window buffer.

---

## 🧪 Testing

Run the full automated test suite using `pytest`:

```bash
# Run all unit and integration tests
py -m pytest -v

# Run specific modules
py -m pytest tests/test_streaming.py -v
py -m pytest tests/test_pii_engine.py -v
py -m pytest tests/test_security_hardening.py -v
```

---

## 🚀 Enterprise Deployment & Operations

Designed for zero-friction adoption by DevOps, Site Reliability Engineers (SREs), and Network Administrators:

### 1. 🏥 Health Probes & CORS Preflight Exemptions
Built-in liveness, readiness, and metrics endpoints explicitly support enterprise orchestrators:
- **Kubernetes / Swarm Probes:** Requests to `/healthz` and `/livez` return an immediate `HTTP 200 OK` liveness probe. Requests to `/readyz` verify Redis connectivity and proxy health.
- **Prometheus Metrics:** Native instrumentation at `/metrics` with optional Bearer token authentication.
- **Frontend / Browser Integration:** Native support for CORS `OPTIONS` preflight requests, returning standard CORS headers and `HTTP 204 No Content` to unblock secure frontend applications without triggering auth failures.

```bash
$ curl -X GET "http://localhost:8000/health"
# Output: {"status":"ok","service":"llm-shield-proxy","version":"1.3.4"}

$ curl -X GET "http://localhost:8000/readyz"
# Output: {"status":"ready","service":"llm-shield-proxy","version":"1.3.4","redis_connected":false}

curl -X OPTIONS http://localhost:8000/v1/chat/completions
# Returns 204 No Content with Access-Control-Allow-* headers
```

### 2. ⚙️ 12-Factor Environment Configuration (`pydantic-settings`)
Configuration follows 12-factor environment-variable practices. Upstream routing, keys, thresholds, and pool sizes are managed via validated `pydantic-settings`:

| Environment Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| **`HOST`** | `str` | `0.0.0.0` | Socket host to bind |
| **`PORT`** | `int` | `8000` | Socket port to bind |
| **`UPSTREAM_BASE_URL`** | `str` | `https://api.openai.com` | Target upstream LLM provider base URL |
| **`OPENAI_API_KEY`** | `str` | `None` | Centralized enterprise OpenAI API key |
| **`REDIS_URL`** | `str` | `None` | Redis connection URL for distributed vault state |
| **`TELEMETRY_ENABLED`** | `bool` | `False` | Enable OpenTelemetry tracing and audit logging to OTLP collector |

> **Note:** For a full list of all configuration flags and advanced feature toggles, refer to the [Deployment Guide](DEPLOYMENT.md).

### 3. 📈 Stateless & Horizontal Scaling
LLM-Shield-Proxy runs completely stateless by default. For high-volume enterprise deployments, instances scale horizontally behind edge proxies (NGINX, Traefik, AWS ALB):
```bash
# Spin up 5 load-balanced instances of the proxy
docker compose up -d --scale llm-shield-proxy=5
```
When configured with `REDIS_URL`, session vaults are shared across all proxy replicas via `redis.asyncio`, ensuring seamless session isolation across multi-instance clusters.

### 4. 🔒 Supply Chain Integrity & GPG Signature Verification
Every published release includes automated SHA-256 checksums (`checksums.txt`) and GPG detached signatures (`checksums.txt.asc`) signed by maintainer **Ninad Phalak**. You can verify checksums and cryptographic authenticity before deployment using:

```bash
# 1. Verify SHA-256 Checksums (Linux / macOS):
sha256sum -c checksums.txt

# On Windows (PowerShell):
Get-FileHash llm-shield-proxy-source-v1.3.4.zip -Algorithm SHA256

# 2. Verify Cryptographic GPG Signature:
gpg --verify checksums.txt.asc checksums.txt
```

### 5. 📜 Policy-as-Code & Hot-Reloading
Abstracts configuration fatigue away from the global environment variables by mounting a `policies.yaml` file to dynamically map `virtual_key_id` client identities to distinct security roles. The engine supports zero-downtime hot-reloading updates for live, enterprise-grade RBAC without dropping active proxy streams.
* **Universal Dynamic Overrides**: Allows per-tenant contextual isolation of any proxy setting (e.g., rate limits, strictness, timeouts) seamlessly natively via [policies.yaml](POLICIES.md).

---

## 🌍 Open Source Roadmap & Contributions

I am committed to maintaining LLM-Shield-Proxy as the fastest ultra-low latency redaction engine for LLMs. I am actively looking for open-source contributors and collaborators to help execute the following technical roadmap. If you submit a PR, I will personally review and merge your architecture contributions:

1. **Cythonize the Sliding-Window Buffer:** Compile the pure-Python async generator (`streaming.py`) into a C-extension binary to aggressively drive down tail latencies for high-throughput enterprise deployments.
2. **Upstream Integration:** Track upstream discussions and context for resolving SSE stream fragmentation in enterprise sandboxes, such as the [NVIDIA/OpenShell #2763](https://github.com/NVIDIA/OpenShell/issues/2763) proposal.

If you want to contribute to enterprise AI security, check out [CONTRIBUTING.md](CONTRIBUTING.md) and claim an issue (e.g., [Help Cythonize the proxy! #15](https://github.com/ninadphalak/LLM-Shield-Proxy/issues/15))!

---

## 🏢 Enterprise Support & Community

If your organization is evaluating, benchmarking, or deploying LLM-Shield-Proxy to unblock LLM streaming and support compliance programs (like SOC 2/HIPAA), I encourage you to engage with the community:

* **Architecture Discussions:** Open a GitHub Discussion to share your feedback on high-throughput deployments, custom proxy pipelines, or benchmark results.
* **Enterprise Case Studies:** If your startup or enterprise is using the proxy in production, let me know! I highlight production architectures and feature enterprise teams in my community benchmarks.
* **Bug Reports & Features:** Submit technical issues or feature requests via the GitHub Issue tracker.

LLM-Shield-Proxy is actively gathering feedback from CISOs, DevOps engineers, and Cybersecurity professionals to shape the open-source compliance roadmap.

---


## 📚 Enterprise Documentation Hub & Feature Matrix

* **[ARCHITECTURE.md](ARCHITECTURE.md) - Engine & Data Plane**
  * [Format-Preserving Synthetic Masking & Entropy (Shannon entropy)](ARCHITECTURE.md#tier-2-shannon-entropy--format-preserving-synthetic-masking)
  * [In-Band Stateless Syntheticgraphic Masking](ARCHITECTURE.md#in-band-stateless-cryptographic-masking)
  * [Multi-Provider Translators (e.g. Zero-SDK OpenAI-to-Anthropic request transformation and SSE stream normalization)](ARCHITECTURE.md#multi-provider-translators--anthropic-adapter)
  * [Anthropic Adapter Implementation](ARCHITECTURE.md#multi-provider-translators--anthropic-adapter)
  * [Bounded Streaming JSON Lexer](ARCHITECTURE.md#zero-allocation-streaming-json-lexer-orjson--rust)
  * [Provider Failover Routing (Explicit header-driven rerouting to secondary mirrors without model downgrades)](ARCHITECTURE.md#provider-failover-routing)
  * [Antifragile Exponential Retries (Native asyncio jitter catching network timeouts and 429/50x errors)](ARCHITECTURE.md#antifragile-exponential-retries)
* **[POLICIES.md](POLICIES.md) - Role-Based Policy-as-Code (RBAC)**
  * [Hierarchical Identity Mapping (Virtual Key to Tenant Roles)](POLICIES.md#1-role-hierarchy--inheritance)
  * [Zero-Downtime Hot-Reloading & File Polling Architecture](POLICIES.md#2-hot-reloading--file-watcher-architecture)
  * [Dynamic Thread-Safe Context Overrides via DynamicSettingsProxy](POLICIES.md#3-dynamic-settings-proxy--thread-safe-contextvars)
  * [Tenant-Scoped PII & NER Engine Detection Profiles](POLICIES.md#4-tenant-scoped-pii--ner-detection-profiles)
* **[SECURITY.md](SECURITY.md) - Threat Model & Defenses**
  * [Composite Agent Loop Circuit Breaker](SECURITY.md#autonomous-agent-security-composite-agent-loop-circuit-breaker)
  * [Stateless Redis TTL Vault & Deterministic HMAC Masking](SECURITY.md#stateless-redis-ttl-vault--deterministic-hmac-masking)
  * [Granular Entity Policy Scopes (O(1) in-memory tenant profile mapping)](SECURITY.md#granular-entity-policy-scopes--zero-trust-ai-defaults)
  * [Centralized Enterprise Secrets & mTLS (Native HashiCorp Vault)](SECURITY.md#centralized-enterprise-secrets--mtls)
  * [Cryptographic Canary Prompt Tripwires (Inbound honeytokens and outbound Generator Exit socket drops)](SECURITY.md#cryptographic-canary-prompt-tripwires)
  * [Entity-Weighted Blast Radius Limits (Redis Token-Bucket circuit breakers for bulk data exfiltration)](SECURITY.md#entity-weighted-blast-radius-limits)
* **[COMPLIANCE.md](COMPLIANCE.md) - Audit, Forensics & Legal**
  * [Cryptographic SHA-256 Hash Chaining](COMPLIANCE.md#cryptographic-audit--tamper-evidence)
  * [Dynamic Canary Watermarking & Steganography (Leak Forensics)](COMPLIANCE.md#dynamic-canary-watermarking--steganography)
  * [Cryptographic Proof of Non-Egress Cryptographic Attestation](COMPLIANCE.md#cryptographic-audit--tamper-evidence)
  * [Tamper-Evident Audit Logging & SHA-256 Hash Chaining](COMPLIANCE.md#cryptographic-audit--tamper-evidence)
  * [FIPS 140-3 KAT, RFC 6902 Differential Audit Logging](COMPLIANCE.md#data-in-transit-encryption--fips-integrity)
  * [LLM FinOps Chargeback Meter (Asynchronous Prometheus metrics for multi-tenant chargebacks)](COMPLIANCE.md#llm-finops-chargeback-meter)
  * [Universal Decision Trace Exporter (NIST OSCAL artifacts and OpenTelemetry spans)](COMPLIANCE.md#universal-decision-trace-exporter)
* **[DEPLOYMENT.md](DEPLOYMENT.md) - Infrastructure & Resiliency**
  * [Service Mesh Native Interface](DEPLOYMENT.md#1-service-mesh-native-interface)
  * [Asynchronous OpenTelemetry Tracing (bounded background delivery)](DEPLOYMENT.md#2-zero-overhead-opentelemetry-tracing)
  * [Service Mesh Native gRPC ext_proc Integration (Zero HTTP network hops)](DEPLOYMENT.md#3-service-mesh-native-grpc-extproc-integration)
  * [Traffic Engineering & Resiliency (Redis evalsha Token-Bucket Rate Limiter, Kubernetes 25s SIGTERM draining)](DEPLOYMENT.md#4-traffic-engineering--resiliency)
  * [Zero-Dependency Kubernetes Mutating Webhook](DEPLOYMENT.md#5-zero-dependency-kubernetes-mutating-webhook)
  * [Deep Component Health Probes and Prometheus Alert Rules](DEPLOYMENT.md#6-deep-component-health-probes-and-prometheus-alert-rules)

## 📄 Intellectual Property & Licensing

**LLM-Shield-Proxy** is an original engineering work authored and maintained by **Ninad Phalak**.

* **Open-Source License:** The core engine, proxy middleware, and streaming buffers are licensed under the **Apache 2.0 License** (see [LICENSE](LICENSE) for details).
* **Patent Status:** Core architectural mechanisms are protected under **U.S. Patent Pending** status:
  * **App. No. 64/126,730**: Protects the asynchronous Server-Sent Event (SSE) sliding-window lookahead buffer and the memory-bounded two-tier inference routing cascade.
  * **App. No. 64/139,263**: Protects the stateless syntheticgraphic JSON-RPC/MCP AST masking, HKDF subkey encryption, and generative AI metadata schema coercion.

---

## 📚 Research & Publications

[![DOI](https://zenodo.org/badge/latestdoi/ninadphalak/LLM-Shield-Proxy)](https://zenodo.org/badge/latestdoi/ninadphalak/LLM-Shield-Proxy)

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



