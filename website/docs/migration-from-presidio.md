# Migrating from Microsoft Presidio in 10 Minutes

Microsoft Presidio is a solid open-source PII detection SDK, but it was built as a batch
NLP toolkit, not a real-time LLM traffic gateway. Bolting it in front of a streaming chat
completion endpoint requires the application team to integrate detection, response-stream handling,
state, and policy enforcement into its request path.

LLM-Shield-Proxy is a drop-in reverse proxy: point your existing `OPENAI_BASE_URL` (or
equivalent) at it, and it transparently redacts PII/PHI/secrets in both directions -
without you touching your Presidio `AnalyzerEngine`/`AnonymizerEngine` call sites at all.

This guide gets you from "Presidio in the request path" to "LLM-Shield-Proxy in front of
your LLM provider" in about 10 minutes.

## Why Teams Migrate

| | Microsoft Presidio (in-process) | LLM-Shield-Proxy (sidecar proxy) |
| :--- | :--- | :--- |
| **Deployment model** | Python library called inline in your app | Reverse proxy / sidecar - zero app-code coupling |
| **Memory footprint** | Depends on selected analyzers and models | Standard mode avoids a neural runtime; measure both candidates with the same workload |
| **Latency** | Depends on analyzer, model, and integration | Isolated entropy and full request paths are reported separately; compare both products under the same service-level protocol |
| **Streaming (SSE) support** | Requires integration-specific handling | Native bounded sliding-window SSE rehydration; overhead is environment-scoped |
| **Secret detection** | Requires custom recognizers | Built-in Tier-2 Shannon-entropy scanner for unformatted API keys/hashes/tokens |
| **Audit trail** | No equivalent gateway trail by default | Process-local SHA-256 hash chain and Ed25519 receipts; durable local mode is opt-in and WORM retention is external |

## Step 1: The 1-Line Python SDK Change

If your code currently looks like this:

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-...",
    base_url="https://api.openai.com/v1",
)
```

Change the `base_url` to point at your LLM-Shield-Proxy instance. That's the entire code
change - no Presidio `AnalyzerEngine()` / `AnonymizerEngine()` calls to rip out of your
request path, no custom recognizers to port:

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-...",
    base_url="http://localhost:8000",  # <- was https://api.openai.com/v1
)
```

Everything else - streaming, tool calls, function calling, retries - behaves exactly as
before. The proxy redacts PII on the way out and transparently rehydrates it on the way
back in, so your application code never sees masked tokens.

## Step 2: Docker Compose Drop-In

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
- LLM-Shield-Proxy additionally detects unformatted high-entropy secrets (raw API keys,
  hashes, tokens) via Shannon entropy analysis - a class of secret Presidio's
  pattern-based recognizers typically miss unless you hand-write a regex for every key
  format.

## Benchmark Callout

The comparison below follows LLM-Shield-Proxy's public [conformance
protocol](/docs/conformance/specification-v1), re-run automatically on every push via
[`.github/workflows/benchmark.yml`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/.github/workflows/benchmark.yml).
The published result separates component observations from service-level claims so the
scope stays independently checkable:

| Metric | Microsoft Presidio (spaCy/PyTorch) | LLM-Shield-Proxy |
| :--- | :--- | :--- |
| **Resident memory** | Deployment and analyzer dependent | Measure peak RSS with the same corpus and service topology |
| **Latency** | Analyzer and integration dependent | Isolated entropy and service-level paths are measured separately |
| **Streaming (SSE) support** | Requires integration-specific handling | Native bounded sliding-window rehydration |

## What You Keep, What You Gain

You keep: your existing LLM client code (`openai`, `anthropic`, LangChain, LiteLLM -
anything that takes a `base_url`), your provider API keys, your prompts.

You gain: a testable configured-upstream redaction layer, native SSE
streaming support, a SHA-256 hash-chained and **Ed25519-signed audit trail** for
every redaction decision, and NIST OSCAL-formatted compliance evidence you can export
in one command (`llm-shield-proxy compliance-report --framework=hipaa`).

## Next Steps

- [Deployment Topologies](/docs/deployment) - VPC and air-gapped egress gateway setups.
- [Enterprise Auditing & Compliance](/docs/features/enterprise-auditing-compliance) - tamper-evident chaining, signed receipts, and OSCAL export; immutable retention is a deployment control.
- [LiteLLM & Ollama Recipe](/docs/litellm-ollama-recipe) - if you're routing through LiteLLM or running local models.
