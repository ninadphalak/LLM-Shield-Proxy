# Evaluating a Migration from Microsoft Presidio

Microsoft Presidio is a solid open-source PII detection SDK, but it was built as a batch
NLP toolkit, not a real-time LLM traffic gateway. Bolting it in front of a streaming chat
completion endpoint requires the application team to integrate detection, response-stream handling,
state, and policy enforcement into its request path.

LLM-Shield-Proxy is a reverse proxy with a supported OpenAI-compatible subset. An evaluation can
start by pointing a test client's base URL at it, but replacing Presidio requires a detector-quality,
payload-shape, streaming, tool-call, policy, and failure-path comparison for the application.

This guide provides a starting configuration; it does not estimate production migration time.

## Why Teams Migrate

| | Microsoft Presidio (in-process) | LLM-Shield-Proxy (sidecar proxy) |
| :--- | :--- | :--- |
| **Deployment model** | Python library called inline in your app | Reverse proxy / sidecar; client or mesh routing changes are still required |
| **Memory footprint** | Depends on selected analyzers and models | Standard mode avoids a neural runtime; measure both candidates with the same workload |
| **Latency** | Depends on analyzer, model, and integration | Isolated entropy and full request paths are reported separately; compare both products under the same service-level protocol |
| **Streaming (SSE) support** | Requires integration-specific handling | Native bounded sliding-window SSE rehydration; overhead is environment-scoped |
| **Secret detection** | Requires custom recognizers | Built-in Tier-2 Shannon-entropy scanner for unformatted API keys/hashes/tokens |
| **Audit trail** | No equivalent gateway trail by default | Process-local SHA-256 hash chain and Ed25519 receipts; durable local mode is opt-in and WORM retention is external |

## Step 1: Change a Test Client's Base URL

If your code currently looks like this:

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-...",
    base_url="https://api.openai.com/v1",
)
```

Change the `base_url` in a non-production test to point at LLM-Shield-Proxy. Existing Presidio
recognizers and application-specific behavior still need to be inventoried and compared:

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-...",
    base_url="http://localhost:8000",  # <- was https://api.openai.com/v1
)
```

Do not assume streaming, tools, function calling, provider fields, errors, or retries behave
identically. The proxy can rehydrate supported intact tokens on the return path; provider or
application transformations can prevent that, and unsupported shapes may behave differently.

## Step 2: Docker Compose Evaluation

If Presidio was running as its own container (or an in-process dependency) in front of
your LLM calls, replace it with the LLM-Shield-Proxy sidecar:

```yaml
services:
  # Remove: presidio-analyzer, presidio-anonymizer containers/dependencies

  # Add: LLM-Shield-Proxy sidecar
  llm-shield-proxy:
    image: ninadphalak/llm-shield-proxy:latest
    ports:
      - "8000:8000"
    environment:
      - UPSTREAM_BASE_URL=https://api.openai.com
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      # Optional: enable Tier 3 contextual NER; benchmark the selected model's RSS and latency
      # - ENABLE_TIER3_ONNX_NER=true
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')\" || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 5s
```

Then point your application at `http://llm-shield-proxy:8000` instead of the Presidio
service.

## Step 3: Entity Mapping

Presidio's recognizers and LLM-Shield-Proxy's detection cascade cover overlapping ground
using different entity names. If you have compliance policies or dashboards keyed on
Presidio entity types, use this table to re-map them:

| Presidio Entity | LLM-Shield-Proxy Entity | Detection Tier |
| :--- | :--- | :--- |
| `PERSON` | `PERSON` | Tier 3 (Contextual NER, ONNX) |
| `EMAIL_ADDRESS` | `EMAIL` | Tier 1 (Regex) |
| `PHONE_NUMBER` | `PHONE` | Tier 1 (Regex) |
| `US_SSN` | `SSN` | Tier 1 (Regex) |
| `CREDIT_CARD` | `CREDIT_CARD` | Tier 1 (Regex) |
| `IP_ADDRESS` | `IP_ADDRESS` | Tier 1 (Regex) |
| `MEDICAL_LICENSE` | `MRN` | Tier 1 (Regex) |
| `CRYPTO` / custom API-key recognizers | `AWS_API_KEY`, `GITHUB_PAT`, `JWT_TOKEN` | Tier 1 (Regex) |
| Custom high-entropy-secret recognizers | `SECRET_KEY` | Tier 2 (Shannon Entropy) |
| `NRP`, `LOCATION`, `ORGANIZATION` (via custom recognizers) | Contextual NER coverage | Tier 3 (Contextual NER, opt-in) |

**Notes:**
- Tier 1 and Tier 2 (regex + Shannon entropy) ship enabled by default in the base
  `pip install llm-shield-proxy` package. Measure RSS and latency in your environment.
- Tier 3 (`ENABLE_TIER3_ONNX_NER=true`, `pip install "llm-shield-proxy[ner]"`) adds a
  quantized ONNX BERT-NER model for conversational entities (names, organizations) that
  Presidio would normally catch via an NLP analyzer. Compare memory and quality using the same corpus.
- LLM-Shield-Proxy can flag configured high-entropy token candidates. Compare false positives
  and false negatives against the Presidio recognizers and corpus actually in use.

## Benchmark Callout

The comparison below uses the scope defined by LLM-Shield-Proxy's public [conformance
protocol](/docs/conformance/specification-v1). The repository workflow is intended to run on pushes; verify the status and artifact for the revision being evaluated via
[`.github/workflows/benchmark.yml`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/.github/workflows/benchmark.yml).
The published result separates component observations from service-level claims so the
scope stays independently checkable:

| Metric | Microsoft Presidio (spaCy/PyTorch) | LLM-Shield-Proxy |
| :--- | :--- | :--- |
| **Resident memory** | Deployment and analyzer dependent | Measure peak RSS with the same corpus and service topology |
| **Latency** | Analyzer and integration dependent | Isolated entropy and service-level paths are measured separately |
| **Streaming (SSE) support** | Requires integration-specific handling | Native bounded sliding-window rehydration |

## What You Keep, What You Gain

You may be able to retain clients that can target the proxy's supported protocol subset. Provider
keys, prompt contracts, adapters, and tool schemas still require integration-specific review.

The evaluation adds a testable configured-upstream redaction layer, bounded SSE rehydration,
hash-chained and signed audit events on instrumented paths, and an OSCAL-formatted report export.
Coverage, durability, key custody, and control effectiveness remain configuration- and path-specific.

## Next Steps

- [Deployment Topologies](/docs/deployment) - VPC and air-gapped egress gateway setups.
- [Enterprise Auditing & Compliance](/docs/features/enterprise-auditing-compliance) - tamper-evident chaining, signed receipts, and OSCAL export; immutable retention is a deployment control.
- [LiteLLM & Ollama Recipe](/docs/litellm-ollama-recipe) - if you're routing through LiteLLM or running local models.
