# LLM-Shield-Proxy

[![Build Status](https://github.com/ninadphalak/LLM-Shield-Proxy/actions/workflows/ci.yml/badge.svg)](https://github.com/ninadphalak/LLM-Shield-Proxy/actions/workflows/ci.yml)
[![PyPI: llm-shield-proxy](https://img.shields.io/pypi/v/llm-shield-proxy.svg?color=green&label=llm-shield-proxy)](https://pypi.org/project/llm-shield-proxy/)
[![PyPI: pii-leak-benchmark](https://img.shields.io/pypi/v/pii-leak-benchmark.svg?color=green&label=pii-leak-benchmark)](https://pypi.org/project/pii-leak-benchmark/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![Docs & Playground](https://img.shields.io/badge/docs-browser%20playground-00a878)](https://project-0039f5fd-ac66-4a1c-9e0.web.app)

This repository is two things, and the first one matters more:

1. **[`pii-leak-benchmark`](pii-leak-benchmark/)** - a neutral harness that measures whether *any*
   OpenAI-compatible streaming gateway sends raw personal data to its upstream, and whether it gives
   the values back to the client.
2. **LLM-Shield-Proxy** - a streaming privacy gateway. It is one of the things the benchmark
   measures, labelled in the table as the reference implementation, and its row carries the same
   `unreplicated` caveat as everyone else's.

## The first result exposed a biased fixture

The benchmark and LLM-Shield-Proxy share a maintainer. That is a conflict of interest, so every
self-run result is labelled `unreplicated` and published with its configuration and raw report for
other people to check.

The prompt used to carry three fixed values, chosen to be safe to publish: `person@example.invalid`,
`123-45-6789`, `4532-1234-5678-9012`. Every one is a value a **validating** detector is built to
reject: `.invalid` has no public suffix, that SSN is a blacklisted sequence, and the card fails its
Luhn checksum (sum 68). Measured against a pinned Presidio at `score_threshold: 0.0`, stock Presidio
returned no `EMAIL_ADDRESS`, no `US_SSN` and no `CREDIT_CARD` for any of them.

This project's engine used regexes without those validation checks, so it caught all three. The
fixture therefore favored this project's detector design. The problem surfaced in a third-party
test: LiteLLM+Presidio
reported `leaked: ["SSN"]` on the shipped fixture and `leaked: []` on the same run with valid
specimens. That row was withheld rather than published, the fixture was replaced, and every row was
re-run. Full measurement: [fixture threat model](website/docs/conformance/fixture-threat-model.md).

The benchmark also exposed two defects in this proxy's streaming hot path: an OpenTelemetry
span opened per SSE delta even with export disabled, and the data line and its terminating blank
line yielded as two separate ASGI writes. Both are fixed and pinned by
[`tests/test_streaming_write_efficiency.py`](tests/test_streaming_write_efficiency.py). No speed
multiplier is published for that fix: the original runner and its raw samples were not retained, so
there is no auditable evidence to cite. See [the record](benchmarks/results/http-profile-llm-shield-proxy-working-tree.md).

**Known limitation:** a roughly 35-line `str.replace` shim with no general detector can pass all
five checks. The formats remain fixed because broader format randomization produced false leak
findings in two of six variants against a correctly redacting gateway. Values now vary within those
formats, and submitted CI reports can carry detached provenance over the finished JSON bytes.

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

Standard library plus `httpx` - you should not have to install one gateway to measure another. The
harness stands a capture server in front of the gateway's configured upstream and inspects **every
channel** it could arrive through: request line, method, headers, chunk extensions, trailers and the
decoded JSON body. Anything it cannot inspect fails closed. Ten adversarial rounds are recorded in
[the conformance docs](website/docs/conformance/index.md); the rule that survived them is *enumerate
the channel, not the encoding*.

A measurement is not a verdict. `fail` means one thing only: protected data reached the capture. A
gateway that never claimed to redact anything, or that anonymizes one-way and leaks nothing, gets a
non-verdict outcome instead. This avoids calling a product a privacy failure for a capability it
never claimed to provide.

## Results

| Target | Outcome | Runs / distinct submitters |
| :--- | :--- | :--- |
| Raw capture endpoint (control) | `fail` - three literal matches | 1 / 1 - control, not a product |
| **LLM-Shield-Proxy** (reference implementation) | `pass` - 5/5 | **1 / 1 - unreplicated** |
| LiteLLM 1.99.0, default | `redaction-not-enabled` | 1 / 1 - unreplicated |
| LiteLLM 1.99.0 + Presidio | `no-leak-profile-not-met` (no leak) | 1 / 1 - unreplicated |
| Portkey OSS 1.15.2, default | `redaction-not-enabled` | 1 / 1 - unreplicated |
| Portkey OSS 1.15.2 + regexReplace | `no-leak-profile-not-met` (no leak) | 1 / 1 - unreplicated |

**Every row is unreplicated.** A gateway needs 3 runs from 3 distinct submitters before its result
is treated as replicated. The current rows are maintainer-run measurements, not independent
reproductions. Each one includes the pinned configuration and raw artifact so others can verify or
contradict it.
[Full table, method and evidence](website/docs/conformance/results.md) ·
[submit a run](website/docs/conformance/submitting.md).

## The reference implementation

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

A real completion needs an upstream key and a client-auth configuration. Copy
[`.env.example`](.env.example) and follow the [deployment guide](website/docs/deployment.md) rather
than treating the health check as a production validation.

### How the reference implementation works

Inbound, the proxy detects configured sensitive values and replaces them before the request crosses
the upstream boundary. Outbound, a bounded prefix-aware buffer reconstructs placeholders split
across SSE chunks and restores registered values as the stream continues. Structured JSON payloads
take a separate syntax-preserving mutation path and still require provider/tool schema testing.

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

Standard mode keeps detection, masking, policy and rehydration inside the operator-controlled
gateway, then sends only the transformed request to the selected external provider:

<a href="website/docs/assets/diagram-standard.svg?v=3">
  <img src="website/docs/assets/diagram-standard.svg?v=3" alt="Standard LLM privacy gateway deployment" width="900" />
</a>

Air-gapped mode instead sends transformed traffic to an operator-controlled internal model
gateway. Network policy must still prevent bypass, telemetry and other unintended egress:

<a href="website/docs/assets/diagram-airgapped.svg?v=3">
  <img src="website/docs/assets/diagram-airgapped.svg?v=3" alt="Air-gapped LLM egress gateway deployment" width="900" />
</a>

See [deployment topologies](website/docs/features/deployment-topologies.md),
[air-gapped egress](website/docs/features/air-gapped-egress.md), and the
[Kubernetes/Helm deployment guide](website/docs/deployment.md).

Every catalogued feature carries a `Supported` / `Beta` / `Experimental` / `Research` badge naming
its verification boundary: [feature catalog](website/docs/features-overview.md) ·
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

- [Interactive documentation and playground](https://project-0039f5fd-ac66-4a1c-9e0.web.app)
- [Conformance: spec, results, reproduction, submission](website/docs/conformance/index.md)
- [Feature catalog](website/docs/features-overview.md) · [deployment](website/docs/deployment.md) ·
  [operations](website/docs/operations.md) · [policy as code](website/docs/policies.md)
- [Compliance evidence mapping](COMPLIANCE.md) · [30-day design-partner pilot](website/docs/design-partner-pilot.md)
- [Integrations](website/docs/integrations.md) - LiteLLM, Open WebUI, LangChain, LlamaIndex, Ollama, Envoy
- [Security model](website/docs/security.md) · [limitations](LIMITATIONS.md) ·
  [troubleshooting](website/docs/troubleshooting.md)

## Contributing, license, and citation

Contributions are welcome through [issues](https://github.com/ninadphalak/LLM-Shield-Proxy/issues),
[discussions](https://github.com/ninadphalak/LLM-Shield-Proxy/discussions) and
[CONTRIBUTING.md](CONTRIBUTING.md). The most valuable contribution is a benchmark run against a
gateway you operate, especially one that contradicts a row above.

Source code is Apache 2.0; documentation and diagrams may carry CC BY 4.0 terms. See
[LICENSE](LICENSE).

The author identifies U.S. application numbers **64/126,730** and **64/139,263** as pending filings
related to streaming transformation and structured stateless masking. Pending applications are not
issued patents; verify status with counsel and official records before relying on them.

If you reference the architecture or benchmark methodology, use [CITATION.cff](CITATION.cff) or:

> Phalak, N. (2026). *Quantifying Latency and Token Overhead in Real-Time LLM Stream
> Sanitization: A Tiered Detection Approach*. https://doi.org/10.5281/zenodo.21955770
