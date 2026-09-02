# LLM-Shield-Proxy

[![Build Status](https://github.com/ninadphalak/LLM-Shield-Proxy/actions/workflows/ci.yml/badge.svg)](https://github.com/ninadphalak/LLM-Shield-Proxy/actions/workflows/ci.yml)
[![PyPI: llm-shield-proxy](https://img.shields.io/pypi/v/llm-shield-proxy.svg?color=green&label=llm-shield-proxy)](https://pypi.org/project/llm-shield-proxy/)
[![PyPI: pii-leak-benchmark](https://img.shields.io/pypi/v/pii-leak-benchmark.svg?color=green&label=pii-leak-benchmark)](https://pypi.org/project/pii-leak-benchmark/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![Docs & Playground](https://img.shields.io/badge/docs-browser%20playground-00a878)](https://project-0039f5fd-ac66-4a1c-9e0.web.app)

This repository contains two related packages:

1. **[`pii-leak-benchmark`](pii-leak-benchmark/)** tests an OpenAI-compatible streaming gateway. It
   checks whether the gateway sends the test values to its model provider and whether the client
   gets the original values back.
2. **LLM-Shield-Proxy** is a self-hosted streaming privacy gateway. The benchmark tests it by name
   and applies the same publication rules used for every other gateway.

## What changed after the first benchmark run

The six results below were produced by this project on one workstation. No outside contributor has
repeated them yet, so the table marks every product result as `unreplicated`. Each result links to
the exact configuration and the report produced by the run.

The first version of the test used invalid examples of an email address, SSN, and credit card:

| Old test value | Why Presidio rejected it |
| :--- | :--- |
| `person@example.invalid` | `.invalid` is not a public domain suffix |
| `123-45-6789` | Presidio blocks this well-known invalid SSN sequence |
| `4532-1234-5678-9012` | The number fails the Luhn card-number checksum |

LLM-Shield-Proxy matched the text patterns but did not perform those validity checks. This gave it
an unfair advantage over detectors that validate values. A LiteLLM and Presidio run revealed the
problem: the old values produced `leaked: ["SSN"]`, while valid test values produced `leaked: []`.
The project did not publish the affected result. It replaced the three values with valid, reserved
test values and reran all six configurations. See the
[fixture threat model](website/docs/conformance/fixture-threat-model.md) for the full record.

The benchmark also found two streaming bugs in LLM-Shield-Proxy. It created an OpenTelemetry span
for every SSE event even when export was off, and it sent each event's blank terminator as a
separate write. Both bugs are fixed and covered by
[`tests/test_streaming_write_efficiency.py`](tests/test_streaming_write_efficiency.py). The
[run record](benchmarks/results/http-profile-llm-shield-proxy-working-tree.md) explains what changed.

**Known limitation:** the test uses three fixed data formats. A small program written specifically
for those formats can pass without being a general PII detector. The values change on every run,
but the formats do not. Testing more formats caused two false failures in six trials. See the
[fixture threat model](website/docs/conformance/fixture-threat-model.md) for the measurements.

## Run it yourself, in about a minute

```bash
pip install pii-leak-benchmark

# The negative control: no gateway at all, raw pass-through. MUST report outcome=fail.
pii-leak-benchmark \
  --target-base-url capture://self \
  --target-name raw-pass-through-negative-control --target-version 1 \
  --redaction-claimed claimed \
  --redaction-claim-citation https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/website/docs/conformance/reproducing.md \
  --redaction-enabled \
  --redaction-config-reference "synthetic control: declared redaction intentionally absent"

# Your gateway, already configured to send upstream traffic to http://127.0.0.1:8765/v1
pii-leak-benchmark --target-base-url http://127.0.0.1:4000/v1 --target-name your-gateway
```

The only third-party Python dependency is `httpx`; you do not need to install one gateway to test
another. You configure the gateway to use the benchmark's local capture server as its model
provider. The benchmark then checks the URL, headers, HTTP framing, and JSON body for the test values. If
it cannot safely parse part of the request, the run ends with an error instead of assuming that no
value leaked. The [conformance docs](website/docs/conformance/index.md) describe the full method.

`fail` has one narrow meaning: the gateway sent an unmasked test value to the benchmark's capture
server. A product that does not offer PII redaction is marked `not-applicable`, not failed. A
one-way anonymizer that removes the values but does not restore them receives a separate outcome.

## Results

| Target | Outcome | Runs / distinct submitters |
| :--- | :--- | :--- |
| Raw capture endpoint (control) | `fail` - three literal matches | 1 / 1 - control, not a product |
| **LLM-Shield-Proxy** | `pass` - 5/5 | **1 / 1 - unreplicated** |
| LiteLLM 1.99.0, default | `redaction-not-enabled` | 1 / 1 - unreplicated |
| LiteLLM 1.99.0 + Presidio | `no-leak-profile-not-met` (no leak) | 1 / 1 - unreplicated |
| Portkey OSS 1.15.2, default | `redaction-not-enabled` | 1 / 1 - unreplicated |
| Portkey OSS 1.15.2 + regexReplace | `no-leak-profile-not-met` (no leak) | 1 / 1 - unreplicated |

Every product result above was run once by this project's maintainer. It has not yet been repeated
by an independent person. A result becomes `replicated` only after three different people each
submit a run of the same gateway and configuration. Until then, it remains `unreplicated`.
[Full table, method and evidence](website/docs/conformance/results.md) ·
[submit a run](website/docs/conformance/submitting.md).

## Run LLM-Shield-Proxy

```bash
pip install llm-shield-proxy
llm-shield-proxy --host 0.0.0.0 --port 8000
curl http://localhost:8000/healthz
```

For the container path:

```bash
docker compose up -d
curl http://localhost:8000/healthz
python examples/demo.py
```

<img src="website/docs/LLM-Shield-Proxy-paper-v2.gif" width="600" alt="Terminal demonstration of LLM-Shield-Proxy masking and streaming rehydration" />

LLM-Shield-Proxy is a self-hosted privacy gateway for OpenAI-compatible streaming APIs. It applies
configured PII, PHI, PCI and secret transformations before the upstream, then rehydrates the masked
values incrementally as SSE events arrive. Point an existing client at it by changing `base_url`:

```python
from openai import OpenAI

client = OpenAI(api_key="your-shield-virtual-key", base_url="http://localhost:8000/v1")
stream = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Contact Sarah at sarah@example.com."}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")
```

A successful health check only means the server started. To send a model request, add the API key
for your model provider and configure a key that clients will use to call the proxy. Start with
[`.env.example`](.env.example), then follow the [deployment guide](website/docs/deployment.md).

### How LLM-Shield-Proxy works

Before sending a request to the model provider, the proxy finds configured types of sensitive data
and replaces their values. As the provider streams its response, the proxy joins replacement tokens
that were split across SSE events and restores values that the client is allowed to receive. For
structured JSON, it changes string values without changing the JSON syntax. Test this behavior with
the schemas used by your provider and tools.

<a href="website/docs/assets/diagram-dual-pipeline.svg?v=2">
  <img src="website/docs/assets/diagram-dual-pipeline.svg?v=2" alt="LLM privacy proxy dual-pipeline redaction architecture" width="900" />
</a>

The maintained component map and deployment diagrams live in the
[architecture guide](website/docs/architecture.md),
[architecture whitepaper](website/docs/architecture-whitepaper.md), and
[deployment guide](website/docs/deployment.md).

| Area | What is implemented | Where the evidence stops |
|---|---|---|
| Detection | 10 native Tier 1 patterns, Tier 2 Shannon entropy, optional Tier 3 ONNX NER, BYOR rules | [Supported types](website/docs/features/data-protection-pii-redaction/supported-pii-types.md) · no recall guarantee on unlabeled traffic |
| Streaming privacy | Sliding-window SSE rehydration, bounded streaming JSON lexer | [Architecture](website/docs/architecture.md) · [conformance method](website/docs/conformance/index.md) |
| Masking | Synthetic, structural-tag, scrub, operator-keyed stateless crypto | [Masking guide](website/docs/features/data-protection-pii-redaction/format-preserving-synthetic-masking-entropy.md) · plaintext still exists in process memory |
| Security controls | SSRF/DNS-rebinding egress checks, request policy, rate and blast-radius limits, canary tripwires | [Security](website/docs/security.md) · not a substitute for network policy |
| Evidence plane | Hash-linked audit records, Ed25519 receipts, OSCAL output, compliance packs | [Compliance overview](website/docs/compliance-overview.md) · tamper-evident, **not WORM** without [immutable retention](website/docs/immutable-retention.md) |
| MCP governance | Scoped JSON-RPC subset with RBAC and egress policy | Research-scoped; [MCP guide](website/docs/guides/mcp-tool-governance.md) · not a complete MCP transport |

### Deployment choices

In standard mode, detection, masking, policy checks, and value restoration run inside your gateway.
Only the masked request is sent to the external model provider:

<a href="website/docs/assets/diagram-standard.svg?v=3">
  <img src="website/docs/assets/diagram-standard.svg?v=3" alt="Standard LLM privacy gateway deployment" width="900" />
</a>

In air-gapped mode, the masked request goes to an internal model gateway. Network policy must still
block direct provider access, telemetry, and other unintended outbound traffic:

<a href="website/docs/assets/diagram-airgapped.svg?v=3">
  <img src="website/docs/assets/diagram-airgapped.svg?v=3" alt="Air-gapped LLM egress gateway deployment" width="900" />
</a>

See [deployment topologies](website/docs/features/deployment-topologies.md),
[air-gapped egress](website/docs/features/air-gapped-egress.md), and the
[Kubernetes/Helm deployment guide](website/docs/deployment.md).

Every feature is labelled `Supported`, `Beta`, `Experimental`, or `Research`. The label states how
the feature was tested and what remains untested: [feature catalog](website/docs/features-overview.md) ·
[stability policy](STABILITY.md) · [limitations](LIMITATIONS.md).

It supports SOC 2, HIPAA, GDPR, EU AI Act and NIST/ISO evidence programs by supplying technical
controls and artifacts. It does not certify a deployment, guarantee complete detection, or make
network policy optional.

## Verifying this repository

```bash
git clone https://github.com/ninadphalak/LLM-Shield-Proxy.git
cd LLM-Shield-Proxy
python -m pip install -e ./pii-leak-benchmark -e ".[dev]"
python -m pytest
```

The benchmark is a separate distribution in this repo, so it installs first; nothing in it imports
the proxy and a test fails if that ever changes. CI provisions real Redis, an HTTP/2 ALPN server, a
checksum-pinned ONNX export, Docker, Helm and promtool. A missing dependency fails those jobs
rather than skipping them, so a green build cannot mean "nothing ran".

## Documentation

- **Start here:** [interactive docs and playground](https://project-0039f5fd-ac66-4a1c-9e0.web.app) ·
  [configuration](.env.example) · [deployment](website/docs/deployment.md) ·
  [operations](website/docs/operations.md) · [troubleshooting](website/docs/troubleshooting.md)
- **Understand the design:** [architecture](website/docs/architecture.md) ·
  [architecture whitepaper](website/docs/architecture-whitepaper.md) ·
  [feature catalog](website/docs/features-overview.md) · [stability](STABILITY.md) ·
  [limitations](LIMITATIONS.md)
- **Integrate it:** [integration index](website/docs/integrations.md) ·
  [LiteLLM and Ollama recipe](website/docs/litellm-ollama-recipe.md) ·
  [Open WebUI and LangChain recipe](website/docs/openwebui-langchain-recipe.md) ·
  [migration from Presidio](website/docs/migration-from-presidio.md)
- **Operate securely:** [security model](website/docs/security.md) ·
  [policy as code](website/docs/policies.md) ·
  [compliance evidence mapping](website/docs/compliance-overview.md) ·
  [immutable retention](website/docs/immutable-retention.md)
- **Verify and participate:** [conformance method and results](website/docs/conformance/index.md) ·
  [submit a reproduction](website/docs/conformance/submitting.md) ·
  [30-day design-partner pilot](website/docs/design-partner-pilot.md)

## Contributing, license, and citation

Contributions are welcome through [issues](https://github.com/ninadphalak/LLM-Shield-Proxy/issues),
[discussions](https://github.com/ninadphalak/LLM-Shield-Proxy/discussions) and
[CONTRIBUTING.md](CONTRIBUTING.md). The most valuable contribution is an independent benchmark run
against a gateway you operate, whether it matches or differs from a row above.

Source code is Apache 2.0; documentation and diagrams may carry CC BY 4.0 terms. See
[LICENSE](LICENSE).

The author identifies U.S. application numbers **64/126,730** and **64/139,263** as pending filings
related to streaming transformation and structured stateless masking. Pending applications are not
issued patents; verify status with counsel and official records before relying on them.

If you reference the architecture or benchmark methodology, use [CITATION.cff](CITATION.cff) or:

> Phalak, N. (2026). *Quantifying Latency and Token Overhead in Real-Time LLM Stream
> Sanitization: A Tiered Detection Approach*. https://doi.org/10.5281/zenodo.21955770
