# LLM-Shield-Proxy

[![Build Status](https://github.com/ninadphalak/LLM-Shield-Proxy/actions/workflows/ci.yml/badge.svg)](https://github.com/ninadphalak/LLM-Shield-Proxy/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/llm-shield-proxy.svg?color=green)](https://pypi.org/project/llm-shield-proxy/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![Docs & Playground](https://img.shields.io/badge/docs-browser%20playground-00a878)](https://project-0039f5fd-ac66-4a1c-9e0.web.app)

**10 built-in Tier 1 PII and secret patterns, each held by redaction and rehydration tests.**
[Try the browser-local playground](https://project-0039f5fd-ac66-4a1c-9e0.web.app) or
[inspect the focused tests](tests/test_pii_engine.py).

LLM-Shield-Proxy is an open-source, self-hosted **LLM firewall and privacy proxy** for
OpenAI-compatible streaming APIs. It applies configured PII, PHI, PCI, credential, and secret
transformations before the selected upstream; incrementally rehydrates Server-Sent Events (SSE);
and exposes policy, egress, and audit controls for AI gateway deployments.

It can support Data Loss Prevention (DLP), Zero Trust AI, SOC 2, HIPAA, GDPR, EU AI Act, and
NIST/ISO evidence programs. It does not certify a deployment, guarantee complete detection, or
make network policy optional. Start with [Limitations and assurance boundaries](LIMITATIONS.md).

## See it in five minutes

The playground runs in the browser. To verify the native structured detectors from source:

```bash
git clone https://github.com/ninadphalak/LLM-Shield-Proxy.git
cd LLM-Shield-Proxy
python -m pip install -e ".[dev]"
python -m pytest -q tests/test_pii_engine.py
```

To start the reference proxy locally:

```bash
python -m pip install "llm-shield-proxy[proxy]"
llm-shield-proxy
curl http://localhost:8000/healthz
```

Or build the checked-out source with Docker:

```bash
docker compose up -d --build
curl http://localhost:8000/healthz
```

An actual completion requires an upstream key and an accepted client-auth configuration. Copy
[`.env.example`](.env.example), then follow the [deployment guide](website/docs/deployment.md)
rather than treating the health check as a production validation.

## Architecture at a glance

The proxy chooses a transformation path based on the payload shape. Text prompts use the
configured detector and masking mode. Structured JSON-RPC values are parsed and mutated as data,
which preserves JSON syntax but can still change schema types and requires provider/tool echo
testing.

<a href="website/docs/assets/diagram-dual-pipeline.svg?v=2">
  <img src="website/docs/assets/diagram-dual-pipeline.svg?v=2" alt="LLM privacy proxy dual-pipeline redaction architecture" width="900" />
</a>

### Inbound: detect and transform

1. An OpenAI-compatible client sends a prompt or supported structured payload to the proxy.
2. The enabled cascade evaluates pre-compiled structured patterns, entropy candidates, and an
   optional operator-supplied ONNX NER model.
3. The selected mode substitutes synthetic values, structural tags, one-way scrub markers, or
   operator-keyed AES-GCM tokens.
4. The transformed request is sent to the configured upstream. The boundary is testable, but it
   is not packet capture or proof that unrelated routes cannot egress.

### Outbound: incremental SSE rehydration

1. The upstream response arrives as SSE chunks.
2. A bounded prefix-aware buffer reconstructs placeholders split across transport boundaries.
3. Registered substitutions are rehydrated as the stream continues; paraphrased, omitted, or
   normalized values may not match.

The maintained component map and diagrams live in the
[architecture guide](website/docs/architecture.md) and
[architecture whitepaper](website/docs/architecture-whitepaper.md).

## What is implemented—and where the evidence stops

| Area | Current implementation | Evidence and boundary |
|---|---|---|
| PII and secret detection | Ten native Tier 1 patterns, Tier 2 Shannon entropy candidates, optional Tier 3 ONNX NER, and BYOR rules | [Supported types](website/docs/features/data-protection-pii-redaction/supported-pii-types.md) · [stability tiers](STABILITY.md) |
| Streaming privacy | Sliding-window SSE rehydration and a bounded streaming JSON lexer | [Architecture](website/docs/architecture.md) · [conformance method](website/docs/conformance/index.md) |
| Masking | Synthetic, structural-tag, scrub, and operator-keyed stateless crypto modes | [Masking guide](website/docs/features/data-protection-pii-redaction/format-preserving-synthetic-masking-entropy.md) · [limitations](LIMITATIONS.md) |
| LLM security controls | SSRF/DNS-rebinding checks, request policy, rate and blast-radius controls, and canary tripwires | [Security](website/docs/security.md) · [feature catalog](website/docs/features-overview.md) |
| Evidence plane | Hash-linked audit records, Ed25519 receipts, OSCAL output, and compliance-pack export | [Compliance overview](website/docs/compliance-overview.md) · [immutable retention](website/docs/immutable-retention.md) |
| MCP tool governance | Scoped JSON-RPC methods with RBAC, PII sanitization, and egress policy | Research-scoped subset; see [MCP guide](website/docs/guides/mcp-tool-governance.md) |

The complete catalog labels every item `Supported`, `Beta`, `Experimental`, or `Research` and
names the verification boundary: [Feature catalog](website/docs/features-overview.md) ·
[Stability policy](STABILITY.md).

## Masking and state choices

| Mode | Reversible | Mapping state | Main trade-off |
|---|---:|---|---|
| `SYNTHETIC` | Yes | In-memory or Redis vault | Preserves value shape; synthetic output is not a guarantee of semantic equivalence. |
| `STRUCTURAL_TAG` | Yes | In-memory or Redis vault | Explicit tokens such as `[EMAIL_1]`; model output must preserve them. |
| `SCRUB` | No | None | One-way removal; original values cannot be restored. |
| `STATELESS_CRYPTO` | Yes | Ciphertext carried in-band | Requires `SHIELD_ENCRYPTION_KEY`; schema and echo behavior must be validated. |

Plaintext still exists in process memory during transformation. Redis TTL, in-memory expiry, and
stateless crypto have different crash-dump, persistence, backup, replica, and key-custody risks.

## Deployment topologies

Standard egress sends transformed traffic to the selected provider endpoint:

<a href="website/docs/assets/diagram-standard.svg?v=3">
  <img src="website/docs/assets/diagram-standard.svg?v=3" alt="Standard LLM privacy gateway deployment" width="900" />
</a>

Air-gapped egress mode sends it to an operator-controlled internal gateway. Network controls must
prevent direct bypass and unintended telemetry, update, model-download, or error-path egress.

<a href="website/docs/assets/diagram-airgapped.svg?v=3">
  <img src="website/docs/assets/diagram-airgapped.svg?v=3" alt="Air-gapped LLM egress gateway deployment" width="900" />
</a>

See [Deployment topologies](website/docs/features/deployment-topologies.md),
[Air-gapped egress](website/docs/features/air-gapped-egress.md), and the
[Kubernetes/Helm deployment guide](website/docs/deployment.md).

## OpenAI-compatible client path

Existing clients can begin evaluation by changing `base_url`; compatibility is not universal.
Test every request envelope, streaming event, tool schema, error, retry, and provider adapter used
by the application.

```python
from openai import OpenAI

client = OpenAI(
    api_key="your-shield-virtual-key",
    base_url="http://localhost:8000/v1",
)

stream = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Contact Sarah at sarah@example.com."}],
    stream=True,
)

for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")
```

Recipes are available for LiteLLM, Open WebUI, LangChain, LlamaIndex, Ollama, and Envoy:
[Integrations](website/docs/integrations.md).

## MCP security default

`POST /v1/mcp` is a Research-scoped JSON-RPC gateway for `tools/list`, `tools/call`, and
`resources/read`. It is not a complete MCP Streamable HTTP server and does not implement
initialization, capability negotiation, sessions, or GET/SSE.

Empty `allowed_tools` now fails closed by default:

```dotenv
MCP_EMPTY_ALLOWLIST_MODE=DENY_ALL
```

Blocklist-only deployments must explicitly select `BLOCKLIST_ONLY`. Startup then emits a critical
warning that every tool not named in `blocked_tools` is permitted. Configure and test the resolver,
SSRF egress policy, upstream route, and outage behavior using the
[MCP Tool Governance guide](website/docs/guides/mcp-tool-governance.md).

## Conformance and testing

The base package installs the endpoint-neutral conformance command without the reference proxy's
full dependency stack:

```bash
python -m pip install llm-shield-proxy
llm-shield-conformance --help
```

The HTTP profile uses a controlled capture upstream to inspect the serialized configured-upstream
request and a one-character SSE return path. It does not remotely measure process RSS or prove all
network behavior. Read the [reproduction guide](website/docs/conformance/reproducing.md),
[fixture threat model](website/docs/conformance/fixture-threat-model.md), and
[reporting protocol](benchmarks/REPORTING.md) before publishing a comparison.

For repository verification:

```bash
python -m pip install -e ".[dev]"
python -m pytest
cd website
npm install
npm run build
```

Infrastructure-dependent CI jobs provision Redis, an HTTP/2 ALPN server, a pinned ONNX export,
Docker, Helm, and promtool. Missing required infrastructure fails those CI jobs instead of silently
skipping. Exact topology and remaining gaps are listed in [STABILITY.md](STABILITY.md).

## Evaluating alternatives

LLM-Shield-Proxy is a privacy/security layer, not a model router or agent framework. It can sit in
front of LiteLLM, LangChain, LlamaIndex, vLLM, Ollama, NVIDIA NIM, or another OpenAI-compatible
path, subject to integration testing.

When comparing LLM gateways, DLP tools, Microsoft Presidio, spaCy pipelines, hosted AI safety
APIs, or packet-local scanners, measure the same corpus and protocol boundary. Compare:

- detection precision/recall on a labeled, representative dataset;
- raw protected values observed at the configured upstream;
- SSE fragmentation behavior and total end-to-end latency;
- process RSS, concurrency, failure policy, and audit durability;
- provider/tool schema compatibility and operational key custody.

See [Migration from Presidio](website/docs/migration-from-presidio.md) for a scoped comparison.

## Compliance and security boundaries

The project supplies technical controls and evidence artifacts; it does not establish legal or
audit conclusions. Start here:

- [Security model](website/docs/security.md)
- [Limitations and assurance boundaries](LIMITATIONS.md)
- [SOC 2](website/docs/compliance/soc2.md), [HIPAA](website/docs/compliance/hipaa.md),
  [GDPR](website/docs/compliance/gdpr.md), and [EU AI Act](website/docs/compliance/eu_ai_act.md)
- [NIST, ISO, and FIPS boundaries](website/docs/compliance/nist_iso_fips.md)
- [Security policy and vulnerability reporting](SECURITY.md)

## Documentation

- [Interactive documentation and playground](https://project-0039f5fd-ac66-4a1c-9e0.web.app)
- [Feature catalog](website/docs/features-overview.md)
- [Deployment](website/docs/deployment.md) and [operations](website/docs/operations.md)
- [Policy as code](website/docs/policies.md)
- [Troubleshooting](website/docs/troubleshooting.md)
- [Design-partner pilot](website/docs/design-partner-pilot.md)
- [Research and publications](website/docs/research-publications.md)

## Contributing, license, and citation

Contributions are welcome through [issues](https://github.com/ninadphalak/LLM-Shield-Proxy/issues),
[discussions](https://github.com/ninadphalak/LLM-Shield-Proxy/discussions), and
[CONTRIBUTING.md](CONTRIBUTING.md). Source code is Apache 2.0; documentation and diagrams may carry
CC BY 4.0 terms. See [LICENSE](LICENSE).

The author identifies U.S. application numbers **64/126,730** and **64/139,263** as pending
filings related to streaming transformation and structured stateless masking. Pending applications
are not issued patents; verify status with counsel and official records before relying on them.

If you reference the architecture or benchmark methodology, use [CITATION.cff](CITATION.cff) or:

> Phalak, N. (2026). *Quantifying Latency and Token Overhead in Real-Time LLM Stream
> Sanitization: A Tiered Detection Approach*. https://doi.org/10.5281/zenodo.21955770
