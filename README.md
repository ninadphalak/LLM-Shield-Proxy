# LLM-Shield-Proxy 🛡️

[![Build Status](https://github.com/ninadphalak/LLM-Shield-Proxy/actions/workflows/ci.yml/badge.svg)](https://github.com/ninadphalak/LLM-Shield-Proxy/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/llm-shield-proxy.svg?color=green)](https://pypi.org/project/llm-shield-proxy/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![License: CC BY 4.0](https://img.shields.io/badge/License-CC_BY_4.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Docker Pulls](https://img.shields.io/badge/docker-ready-blue.svg)](https://hub.docker.com/)
[![Docs & Live Demo](https://img.shields.io/badge/docs-interactive%20demo-00ff9d)](https://project-0039f5fd-ac66-4a1c-9e0.web.app)

**7/7 conformance domains passed** in the published maintainer pre-release self-test.
[Inspect the machine-readable result](benchmarks/results/conformance-v1.0.0-pre-release-windows.json)
or [try the browser-local playground](https://project-0039f5fd-ac66-4a1c-9e0.web.app).

The result covers declared fixtures and measurement scopes, not an independent production
validation. See [the consolidated limitations and assurance boundaries](LIMITATIONS.md).

<img src="website/docs/LLM-Shield-Proxy-paper-v2.gif" width="600" alt="LLM-Shield-Proxy Demo" />

An open-source, FastAPI-based streaming privacy gateway for OpenAI-compatible traffic. It applies
configured PII/secret transformations before the selected upstream and supports incremental SSE
rehydration, policy enforcement, and signed audit evidence.

> **Apache 2.0:** the source is inspectable, and the proxy can be downloaded and self-hosted without a license fee.

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

The gateway can support selected technical controls and evidence collection for regulated
environments. It is not a compliance certification; see [Limitations](LIMITATIONS.md).

## 🛡️ Dual-Pipeline Redaction Architecture

LLM-Shield-Proxy routes traffic through two redaction pipelines based on the payload structure. Structured payloads are mutated as parsed data rather than by raw string replacement, reducing the risk of invalid JSON; text prompts use the configured detector and masking mode.

<br>
<a target="_blank" href="website/docs/assets/diagram-dual-pipeline.svg?v=2">
  <img src="website/docs/assets/diagram-dual-pipeline.svg?v=2" alt="Dual-Pipeline Redaction Architecture" style="max-width: 100%; height: auto;" />
</a>

### A. Human-to-LLM (Text Prompts / STATELESS_CRYPTO)
For standard conversational text, the proxy respects your configured masking mode. You can choose from four strategies:
1. **SYNTHETIC (Stateful):** (e.g. replacing *'My SSN is 000-00-0000'* with *'My SSN is 111-11-1111'*). Swaps PII with canonical locale fakes (e.g., `John` -> `Maya`). Preserves LLM attention weights and token counts. Requires Redis.
2. **STRUCTURAL_TAG (Stateful):** (e.g. replacing *'My SSN is 000-00-0000'* with *'My SSN is [SSN_1]'*). Swaps PII with explicit bracketed tags (e.g., `[PERSON_1]`). Requires Redis.
3. **SCRUB (Stateless):** (e.g. replacing *'My SSN is 000-00-0000'* with *'My SSN is ***'*). Destructive one-way redaction (`***`). Cannot be rehydrated.
4. **STATELESS_CRYPTO (in-band ciphertext):** (e.g. replacing *'My SSN is 000-00-0000'* with *'My SSN is [enc_3x9kL]'*). Encrypts detected PII in-band via AES-256-GCM using the operator-supplied `SHIELD_ENCRYPTION_KEY`; this mode does not require Redis, but other configured components may still retain ciphertext or request metadata.

### B. Machine-to-Machine (JSON-RPC / Tool Calls)
When the catch-all proxy receives a top-level JSON-RPC `2.0` object, it uses the AST-aware stateless mutation path with the operator-supplied `SHIELD_ENCRYPTION_KEY`. That path rewrites supported string leaves and schemas; downstream schema compatibility and provider echo behavior must be tested. The separate `/v1/mcp` route has its own documented policy and transport boundary.
* **Why?** Blindly running regex over raw JSON strings can corrupt syntax (e.g., matching a JSON key or injecting unescaped characters), causing agent crashes.
* **The Solution:** The proxy parses the payload into an Abstract Syntax Tree (AST), replaces selected leaf values, and can bundle reversible AES-256-GCM context (for example, `{"_shield_val": "Maya", "_shield_ctx": "aesgcm..."}`). AST mutation preserves JSON syntax; provider echo and rehydration behavior must be integration-tested.

---

### 🔥 Enterprise Flagship Features
* **[Fragment-Safe SSE Rehydration](#-how-it-works-the-data-flow):** The sliding-window buffer reconstructs protected placeholders split across Server-Sent Event chunks. Run the reproducible conformance harness below for environment-scoped measurements.
* **[Configured-Boundary Synthetic Masking](#️-dual-pipeline-redaction-architecture):** Local **Data Loss Prevention (DLP) for LLMs** using format-preserving substitution (Regex + Shannon Entropy + optional ONNX NER). The conformance harness checks declared protected values at the configured upstream boundary for the tested configuration.
* **[Air-Gapped Egress Gateway Mode](website/docs/features/air-gapped-egress.md):** Routes the proxy's configured upstream client through an internal egress gateway and can strip provider auth headers. Network policy must separately prevent bypass and other process/container egress.
* **[Configurable Stateless and Ephemeral Masking](#4-in-band-stateless-crypto--ephemeral-vaults):** Stateless AES-256-GCM avoids a mapping database; in-memory and TTL vaults have different memory, persistence, replica, and backup boundaries.
* **[Role-Based Policy-as-Code & Hot-Reloading](website/docs/policies.md):** A YAML poller can map `virtual_key_id` identities to supported roles, PII profiles, and request-scoped setting overrides. Validate reload timing, unknown-identity behavior, and every enabled override in the deployment profile.
* **[Decision Trace Exporter](#-enterprise-compliance-audit-forensics--legal):** Instrumented PII and RBAC paths can emit metadata through the audit, OSCAL, and OpenTelemetry paths. Sampling, queue limits, exporter failures, and retention configuration bound completeness.
* **[Ed25519-Signed Audit Receipts & Evidence Packs](website/docs/features/enterprise-auditing-compliance/ed25519-signed-audit-receipts.md):** Audit events are signed for offline verification (public key at `GET /api/v1/audit/pubkey`); `llm-shield-proxy compliance-report --framework=hipaa` bundles the verification summary, OSCAL results, and a SHA-256 manifest. These artifacts support an audit; they do not certify compliance.
* **[Tool-Call Interception & Agent Governance](website/docs/pluggable-rbac-engine.md):** Parses supported tool-call payloads and applies the configured resolver's allow/block policy before the corresponding upstream action. Resolver defaults and failure modes must be validated.
* **[Context-Aware Tool Catalog Pruner (MCP Discovery)](website/docs/features/ultra-low-latency-streaming-traffic-engineering/context-aware-mcp-discovery-pruner.md):** Intercepts supported JSON-RPC discovery payloads and evaluates tool names against resolved policy before the catalog reaches the model. Redis caching is optional; latency is deployment-specific.
* **[Service Mesh gRPC Sidecar](#5-service-mesh-native-grpc-ext_proc--k8s-sidecar):** Envoy's `ext_proc` can exchange stream buffers with the processor over a Unix Domain Socket; the included Python admission webhook can inject the sidecar configuration. Operators must validate Envoy timeouts, socket permissions, admission controls, and resource overhead.
* **[Bounded Regex Engine](#️-bring-your-own-regex-byor-enterprise-rule-injection):** Supported patterns can use `google-re2` to avoid catastrophic backtracking; validate fallback behavior and unsupported constructs at startup.
* **[Provider Adapters](#-openai-compatible-client-path):** Existing OpenAI-style clients can evaluate the proxy by changing `base_url`; implemented provider adapters cover a documented subset and require field-by-field integration tests.
* **[Edge-Level Agent Identity Enforcer](website/docs/features/agent_identity_enforcer.md):** Validates configured workload identity and DPoP proofs before governed tool calls. Measure latency and exercise replay/failure cases in the pilot profile.



## ⚡ 60-Second Quickstart & Deployment

### 🔄 OpenAI-Compatible Client Path
For clients that use the proxy's supported OpenAI-compatible subset, integration can start by changing the SDK `base_url` or the endpoint in a `curl` command. Test every request shape, streaming mode, tool schema, provider adapter, and error path used by the application; compatibility is not universal and provider selection depends on configuration and request metadata.

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
Start a local proxy and verify its health endpoint.

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

For architectural diagrams showing VPC and Air-Gapped Egress gateway setups, please refer to the **[Deployment Topologies](website/docs/features/deployment-topologies.md)** guide.

LLM-Shield-Proxy is heavily modular. You can configure the engine based on your specific compliance ROI and memory constraints:

| Installation Tier | Command | Capabilities Included | Use Case / Trade-off |
| :--- | :--- | :--- | :--- |
| **Standard Mode** | `pip install llm-shield-proxy` | **Tier 1 (Regex)** & **Tier 2 (Shannon Entropy)** | Best for structured identifiers and secret candidates. It does not provide population-level recall guarantees and can miss conversational names or novel formats. Measure RSS and latency on your workload. |
| **Full NLP Mode**<br>*(Contextual NER)* | `pip install "llm-shield-proxy[ner]"` | Adds **Tier 3 (ONNX Runtime NER)** | Adds a configurable local ONNX model for contextual entities. Quality and memory depend on the selected model and corpus; publish model, dataset, splits, and confidence intervals with any accuracy claim. |

> **Enabling Tier 3 ONNX NER:** When installed with `[ner]`, enable neural entity extraction by setting `ENABLE_TIER3_ONNX_NER=true` in your `.env` or environment variables (and optionally point `ONNX_MODEL_PATH` to custom model weights). If disabled, the Tier 3 detector is not invoked; measure startup and runtime behavior for the selected installation.

### 🔌 Pluggable Extensibility (BYOM & BYOR)
LLM-Shield-Proxy is highly extensible without risking latency or ReDoS.
* **[Bring Your Own Model (BYOM)](website/docs/features/data-protection-pii-redaction/tier-3-quantized-onnx-bert-ner.md):** Configure a compatible ONNX token-classification model for contextual Tier 3 extraction. Validate tokenizer/model compatibility, labels, accuracy, latency, and memory on the intended corpus.
* **[Bring Your Own Regex (BYOR)](website/docs/features/data-protection-pii-redaction/bring-your-own-regex-byor-custom-rules.md):** Load supported `google-re2` patterns for internal proprietary tokens via `custom_regex.yaml`. RE2 avoids catastrophic backtracking for supported constructs; unsupported constructs are rejected or require a documented fallback.

---

## 💥 The Problem vs. The LLM-Shield-Proxy Solution

| Existing Legacy Proxies | LLM-Shield-Proxy |
| :--- | :--- |
| **Destroys Real-Time SSE Streaming:** Buffers entire responses before scanning, causing multi-second UI latency stalls. | **Ultra-Low Latency Streaming:** Redacts and re-hydrates delta-by-delta as SSE packets stream. |
| **Optional heavyweight NLP dependencies:** Some deployments use large NLP runtimes. | **Tiered local processing:** Standard mode avoids a neural runtime; optional ONNX mode has a workload- and model-dependent footprint. |
| **Persistent mapping stores:** Some designs retain PII mappings beyond the request lifecycle. | **Configurable masking state:** Stateless crypto avoids a mapping database; in-memory and Redis modes have different retention, persistence, replica, and backup boundaries that operators must configure and verify. |
| **Inspection API egress:** Some products send source data to a separate inspection service. | **Testable upstream boundary:** scanning occurs in the operator's deployment and the conformance test checks that known raw protected values are absent from the serialized configured-upstream request. |

### 🏛️ Built for Trust & Transparency
Designed specifically for highly regulated enterprise environments, strict **Zero Trust AI** network architectures, and security-first engineering teams implementing **LLM Security Posture Management (LLM SPM)**.
1. **Keeps inspection local:** Deploy the shield inside your boundary and test that unredacted protected data does not reach the configured upstream.
2. **Ephemeral masking state:** Sensitive prompt mappings use in-memory or TTL-backed vaults unless the operator selects an external state store.
3. **Measurable stability:** Run the conformance and load-test protocols in the exact installation mode, host, concurrency, audit, and upstream configuration you plan to operate.
4. **Transparent Rule Engine:** Combines transparent, deterministic pattern matching with Shannon entropy and local ONNX neural entity recognition.

---

## Why Not <s style="color: gray;">Microsoft Presidio</s> <sup>*any other proxy?*</sup>

It's a crowded space. The distinctions below are evaluation criteria, not universal advantages; compare them against your workload and alternatives.

* **Microsoft Presidio / spaCy:** Different detector stacks make different quality, dependency, and resource trade-offs. Compare them on the same corpus and service-level protocol; the project does not currently publish a validated universal memory or total-proxy latency advantage.
* **Cloud AI Safety APIs (Azure/AWS):** A separate hosted inspection call creates an additional data boundary. LLM-Shield-Proxy performs enabled inspection inside the operator-controlled deployment before handing the transformed request to the configured upstream client.
* **Packet-local scanners:** A scanner that examines each SSE chunk independently can miss a token split across chunk boundaries. LLM-Shield-Proxy uses a bounded lookahead window; exercise the published fragmentation fixtures and configure maximum line and payload limits.
* **LiteLLM / LangChain:** LLM-Shield-Proxy is not a model router or orchestration framework. Put it in front of the orchestrator and verify the serialized configured-upstream boundary using the conformance suite.

### 🤝 The Orchestrators (What we complement)
LLM-Shield-Proxy is **not** a model router. It is designed to deploy as an OpenAI-compatible edge proxy in front of orchestration tools. Most clients can be evaluated by changing their base URL; authentication, streaming, tools, provider envelopes, and retry behavior still require integration testing.

* **Orchestration Frameworks:** LangChain, LlamaIndex, Semantic Kernel, AutoGen, CrewAI.
* **AI Gateways & Routers:** LiteLLM is covered by a repository recipe; other gateways should be validated against their current proxy and streaming contracts before being described as supported.
* **Local & Open-Source Inference:** vLLM, Ollama, NVIDIA NIM, Hugging Face TGI.
* **Upstream Providers:** OpenAI, Anthropic, Google Gemini, DeepSeek, Mistral.

Place **LLM-Shield-Proxy** in front of them to apply configured masking and produce evidence that can support SOC 2 control testing before the payload reaches the selected upstream.




---


### How It Works (The Data Flow)

#### 📥 Inbound (Prompt Sanitization)
1. **Intercept:** Your client routes a standard OpenAI / LangChain request through `localhost:8000`.
2. **Dual-Pipeline Routing:** The proxy checks the payload type. Standard text goes to the **3-Tier Cascade Engine** (Regex -> Entropy -> ONNX NER). JSON-RPC tool calls are routed to the **AST-Aware Firewall**.
3. **Secure Substitution:** Sensitive data is swapped out using your configured mode (Synthetic Fakes, Structural Tags, or AES-GCM). Stateful mappings are stored in the local Redis vault; stateless mappings are encrypted in-band.
4. **Configured Upstream:** The transformed payload is handed to the configured upstream client. Use the conformance harness and deployment-level network controls to test the intended boundary.

#### 📤 Outbound (Streaming Rehydration)
1. **SSE Stream Intercept:** The LLM streams the sanitized response back via Server-Sent Events (SSE).
2. **Prefix-Aware Buffer:** Because transports can fragment placeholders across SSE chunks, the sliding-window buffer retains bounded trailing prefix overlap (e.g., `[PER`... `SON_1]`). The conformance suite exercises every two-part split and one-character delivery for its fixtures.
3. **Incremental Rehydration:** When a synthetic name or tag is fully assembled, the proxy retrieves the original data (via Redis or AES decryption) and resumes valid SSE delivery. Measure end-to-end overhead with the published protocol; component microbenchmarks are not total proxy latency.

---

## 🧠 Core Architecture & Technical Innovations

LLM-Shield-Proxy delivers enterprise privacy and zero-trust security through highly optimized architectural breakthroughs.

> **[View the architecture guide](website/docs/architecture.md)** for the maintained component map and evidence boundaries.

### [1. The Data Plane: Bounded Streaming JSON Lexer & SSE Buffer](website/docs/architecture.md)
`orjson` parses fragmented Server-Sent Events while the overlap buffer retains a bounded suffix. The conformance report measures retained-buffer bounds and process allocation separately; it does not assert a universal process-RSS ceiling.

### [2. Pre-compiled Regex Engine (`google-re2` where available)](website/docs/features/data-protection-pii-redaction/tier-1-pre-compiled-regex-engine.md)
Supported identifiers and custom patterns are compiled with `google-re2`, which avoids the catastrophic-backtracking behavior of backtracking regex engines. Validate unsupported constructs and fallback behavior at startup.

### [3. Shannon Entropy Secret Scanner](website/docs/features/data-protection-pii-redaction/tier-2-shannon-entropy-scanner.md)
An O(N) Shannon-entropy operation identifies high-density secret candidates. Its current behavior should be measured with the published benchmark harness.

### [4. Stateless Cryptographic Rehydration (JSON-RPC)](website/docs/features/data-protection-pii-redaction/stateless-ast-aware-semantic-pii-firewall.md)
Dynamically intercepts OpenAI/MCP tool schemas and injects cryptographic context fields into the JSON Schema `required` array. Provider echo behavior is not guaranteed by schema alone and must be tested with the selected model and parser.

---

## 🛡️ Enterprise Security & Threat Defenses

LLM-Shield-Proxy is validated against an exhaustive, continuously growing suite of **170+ automated unit, integration, and adversarial fuzzing tests**.

Below is a high-level navigation summary. The [security model](website/docs/security.md) scopes implemented controls and tested fixtures; it is not a universal vulnerability-coverage matrix.

| Security Domain | Defense Mechanisms & Capabilities |
| :--- | :--- |
| **🛡️ Masking & evidence** | [Masking modes](website/docs/features/data-protection-pii-redaction/4-mode-per-request-masking-pipeline.md), [stateless crypto](website/docs/features/data-protection-pii-redaction/in-band-stateless-cryptographic-masking.md), [Redis TTL vault](website/docs/features/data-protection-pii-redaction/stateless-redis-ttl-vault.md), [watermarking](website/docs/features/enterprise-auditing-compliance/dynamic-canary-watermarking-steganography.md) |
| **🛑 Threat controls** | [Agent breaker](website/docs/features/advanced-threat-defense-enterprise-resilience/composite-agent-loop-circuit-breaker.md), [entity scopes](website/docs/features/data-protection-pii-redaction/granular-entity-policy-scopes.md), [streaming lexer](website/docs/features/ultra-low-latency-streaming-traffic-engineering/zero-allocation-streaming-json-lexer.md), [canary tripwire](website/docs/features/advanced-threat-defense-enterprise-resilience/cryptographic-canary-prompt-tripwires.md), [blast-radius limiter](website/docs/features/advanced-threat-defense-enterprise-resilience/entity-weighted-blast-radius-limits.md) |
| **📜 Audit, Forensics, and Compliance** | 1. Tamper-Evident Audit Logging & SHA-256 Hash Chaining<br>2. Ed25519-signed receipts and checkpoint verification<br>3. Signed Egress Transformation Receipts<br>4. FIPS-oriented KAT and RFC 6902 Differential Audit Logging |
| **🏗️ Infrastructure & service mesh** | [Secrets and mTLS](website/docs/features/secure-infrastructure-service-mesh/centralized-enterprise-secrets-mtls.md), [Envoy ext_proc](website/docs/features/secure-infrastructure-service-mesh/service-mesh-native-grpc-ext-proc-integration.md), [Kubernetes webhook](website/docs/features/secure-infrastructure-service-mesh/zero-dependency-kubernetes-mutating-webhook.md), [traffic controls](website/docs/features/advanced-threat-defense-enterprise-resilience/traffic-engineering-resiliency.md), [failover](website/docs/features/advanced-threat-defense-enterprise-resilience/provider-failover-routing.md) |
| **🔄 Provider adapters** | [Multi-provider translators](website/docs/features/ultra-low-latency-streaming-traffic-engineering/multi-provider-translators.md) and [Anthropic adapter](website/docs/features/ultra-low-latency-streaming-traffic-engineering/anthropic-adapter-implementation.md) |

## 📜 Enterprise Compliance: Audit, Forensics & Legal

LLM-Shield-Proxy is engineered specifically to help enterprises adopt Generative AI while supporting data privacy regulations like HIPAA and SOC 2 audit requirements. These are technical controls that map to specific framework requirements - deploying this proxy is one control among many a full compliance program requires, not a certification or a substitute for legal/compliance review.

Below is a summary of our compliance mappings. For the exhaustive deep-dive mapping, view our [Enterprise Compliance Documentation](COMPLIANCE.md).

### 🛡️ SOC 2 & ISO 42001 Auditor Evidence Mapping
If you are deploying LLM-Shield to satisfy a compliance audit, map the proxy's features directly to your Trust Services Criteria. See our complete [Auditor Evidence Mapping](COMPLIANCE.md).

| Compliance Domain | Supported Features & Capabilities |
| :--- | :--- |
| **🏥 HIPAA transmission-control support** | Enabled local detection and masking can reduce disclosure to the configured upstream. Demonstrate the tested boundary with the capture-upstream conformance profile; this is not a guarantee that undetected or bypass-path PHI cannot egress. |
| **🛡️ SOC 2 Audit Evidence** | SHA-256 hash chaining and Ed25519 signatures emit tamper-evident structured records; durable and immutable retention remain deployment choices. |
| **⚖️ Legal & Egress Provenance** | Signed transformation receipts for the configured application boundary. Optional canary watermarking provides a correlation signal for leak investigations. |
| **🔐 Data Integrity & Storage** | Stateless AES-256-GCM or configured in-memory/Redis mapping modes. Persistence, replicas, backups, process memory, TTL, and key custody remain deployment boundaries. |
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
2. **Pre-compiled RE2 patterns:** Tier 1 patterns are compiled at startup. Matching cost grows with input and pattern behavior; benchmark the complete detector path on workload-shaped payloads.
3. **Native JSON Parsing:** The asynchronous SSE rehydration buffer uses `orjson`; comparative speedups require a published baseline, payload corpus, and environment.
4. **Optional ONNX Neural Pipeline:** Tier 3 is invoked only when enabled and available. Compare installation, import, startup, model-load, RSS, and request-path measurements with Tier 3 disabled and enabled.
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
- **Kubernetes / Swarm Probes:** `/healthz` and `/livez` are shallow liveness aliases. `/readyz` checks selected PII-engine state plus configured Vault-cache and Redis state; it does not test the upstream model endpoint.
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

> **Note:** For the maintained configuration table and feature boundaries, refer to the [Deployment Guide](website/docs/deployment.md).

### 3. 📈 Stateless & Horizontal Scaling
LLM-Shield-Proxy supports a stateless-crypto mode. Stateful masking modes require local or Redis-backed mapping state. Instances can be placed behind edge proxies such as NGINX, Traefik, or AWS ALB after the selected mode is validated for multi-replica routing:
```bash
# Spin up 5 load-balanced instances of the proxy
docker compose up -d --scale llm-shield-proxy=5
```
When configured with `REDIS_URL`, proxy replicas can use the same Redis-backed mapping store via `redis.asyncio`. Validate tenant/session key construction, Redis persistence, failover, TTL, and replica routing before relying on multi-instance rehydration.

### 4. 🔒 Supply Chain Integrity & GPG Signature Verification
When a release publishes `checksums.txt` and `checksums.txt.asc`, verify both against a trusted maintainer key before using the corresponding artifact. Absence of either file means this verification procedure cannot be completed for that release.

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
* **Dynamic overrides**: Applies supported per-tenant settings from [policies.yaml](website/docs/policies.md). Validate each override because not every setting is safe or reloadable at request scope.

---

## 🌍 Open Source Roadmap & Contributions

I am committed to maintaining LLM-Shield-Proxy as a measurable streaming privacy gateway. Contributions are reviewed against correctness, boundary, security, and reproducible performance evidence; submission does not imply that a change will be merged.

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

### 30-Day Design Partner Pilot

Teams in healthcare, legal technology, financial services, security consulting, and other
regulated environments can apply for a confidential, acceptance-criteria-driven evaluation.
Participants run representative data locally and share only aggregate, PII-free artifacts.
See the [pilot scope, eligibility criteria, and application instructions](website/docs/design-partner-pilot.md).

---


## 📚 Documentation hub

* [Architecture and evidence boundaries](website/docs/architecture.md)
* [Security model and tested controls](website/docs/security.md)
* [Compliance-support boundaries](website/docs/compliance-overview.md)
* [Deployment and configuration](website/docs/deployment.md)
* [Feature catalog](website/docs/features-overview.md)
* [Policy-as-code](website/docs/policies.md)
* [Integration examples](website/docs/integrations.md)
* [Open conformance lab](website/docs/conformance/index.md)
* [30-day design-partner pilot](website/docs/design-partner-pilot.md)

## 📄 Intellectual Property & Licensing

**LLM-Shield-Proxy** is an original engineering work authored and maintained by **Ninad Phalak**.

* **Open-Source License:** The core engine, proxy middleware, and streaming buffers are licensed under the **Apache 2.0 License** (see [LICENSE](LICENSE) for details).
* **Patent notice:** The author identifies U.S. application numbers **64/126,730** and
  **64/139,263** as pending filings related to streaming transformation and structured stateless
  masking. A pending application is not an issued patent and this notice makes no representation
  about validity, claim scope, ownership disputes, or eventual grant. Verify status with counsel
  and the relevant official records before relying on it.

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



