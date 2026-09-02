# LiteLLM HTTP profile configuration and results

Two measured rows. Both were executed by the maintainer of LLM-Shield-Proxy against a
locally installed LiteLLM proxy; that is a conflict of interest and is disclosed here
rather than hidden. Neither has been independently reproduced. See
[governance](../../website/docs/conformance/governance.md).

- Harness/source label: `1cef0ff` + the working-tree changes described below
- Target: `litellm[proxy]==1.99.0` under CPython **3.12.3**, `litellm --host 127.0.0.1 --port 4000`
- Controlled upstream: `http://127.0.0.1:8765/v1` (capture mode: `loopback`)
- Request iterations: 3
- Harness host: Windows 11, CPython 3.14.7, AMD64
- Fixture: the current varied, valid, non-real fixture. **Both rows were re-run against
  it**; the earlier results against the old invalid-specimen fixture are superseded and
  the reason is in [the fixture threat model](../../website/docs/conformance/fixture-threat-model.md).

`litellm==1.44.0` is not installable on this machine's CPython 3.14; the proxy was
installed into a separate CPython 3.12 virtualenv so nothing in this repository's
environment was disturbed. Pins that affect the measured path:

```text
litellm==1.99.0
litellm-proxy-extras==0.4.89
litellm-enterprise==0.1.59
openai==2.54.0
fastapi==0.141.1
uvicorn==0.52.4
pydantic==2.13.5
```

`PYTHONIOENCODING=utf-8` is **required** on Windows: without it LiteLLM 1.99.0 aborts
during ASGI startup with `UnicodeEncodeError` while `click.echo`-ing its own banner under
the `cp1252` console codepage. `LITELLM_LOCAL_MODEL_COST_MAP=True` was set so the run
makes no outbound call for the model-cost map.

## Row 1 — default configuration, no PII masking

Artifact: `http-profile-litellm-1.99.0-default.json`
(SHA-256 `dd107de45abd3ef6cf9c3c6f6e6b200e8bf7c8c8a82b932f2128d0f4b296c21c`)

```yaml
model_list:
  - model_name: conformance-model
    litellm_params:
      model: openai/conformance-model
      api_base: http://127.0.0.1:8765/v1
      api_key: <controlled-capture-key>
general_settings:
  master_key: <local-evaluation-key>
litellm_settings:
  telemetry: false
  set_verbose: false
```

Redaction claim recorded: `claimed`, **not** configured for this run, cited to
<https://docs.litellm.ai/docs/proxy/guardrails/pii_masking_v2>.

**Outcome: `redaction-not-enabled`.** A statement about the configuration, not a verdict
about LiteLLM. LiteLLM does not mask PII unless a guardrail is attached, and none was.

| Check | Result |
| :--- | :--- |
| `configured_upstream_boundary` | fail — `leaked_entity_types: [CREDIT_CARD, EMAIL, SSN]`, all three `literal`; `captured=3 correlated=3 uninspectable=0 marker_max=5` |
| `fragmentation_safety` | **pass** — 134 events, response reconstructed |
| `sse_validity` | **pass** — `status_codes: [200]` |
| `response_fidelity` | **pass** |
| `client_observed_latency` | **pass** — 3 of 3 iterations measured |

## Row 2 — Presidio PII masking guardrail enabled

Artifact: `http-profile-litellm-1.99.0-presidio.json`
(SHA-256 `f660fa5b21ce2bdb705b83db1c513ad64aa5392c0c37600e2215fbd131621486`)

```yaml
guardrails:
  - guardrail_name: "presidio-pii-masking"
    litellm_params:
      guardrail: presidio
      mode: "pre_call"
      output_parse_pii: true
      default_on: true
```

```text
PRESIDIO_ANALYZER_API_BASE=http://127.0.0.1:5002
PRESIDIO_ANONYMIZER_API_BASE=http://127.0.0.1:5001
```

Presidio images, pinned by digest, stock recognizer registry, no ad-hoc recognizers:

```text
mcr.microsoft.com/presidio-analyzer@sha256:286e3fa7f3a7426e775e8564fe1870f1ba8f999d3ab8bbb8cc46a44355d9d6e9
mcr.microsoft.com/presidio-anonymizer@sha256:a10a12a2a613d13cf29d3ad3641e3258444dd8c90403dd644a0a114c472c2483
```

**Outcome: `no-leak-profile-not-met`. This is not a leak finding and must not be
published as one.**

| Check | Result |
| :--- | :--- |
| `configured_upstream_boundary` | **pass** — `leaked_entity_types: []`, `leak_evidence: []`; `captured=3 correlated=3 uninspectable=0 marker_max=5` |
| `sse_validity` | **pass** — `status_codes: [200]` |
| `client_observed_latency` | **pass** — 3 of 3 iterations measured |
| `response_fidelity` | fail — the original values are never restored to the client |
| `fragmentation_safety` | fail — gates on the same reconstruction; `events_observed: 1` |

### What this configuration actually does, observed at a logging upstream

```text
client sends   : ... contact bob.smith@example.com, SSN 456-78-9012, card 4111-1111-1111-1111
upstream sees  : ... contact <EMAIL_ADDRESS_1>SN <US_SSN_4>, card <CREDIT_CARD_5>
client receives: ... contact <URL>ith@<URL>SN <US_SSN>, card <CREDIT_CARD>
```

Nothing protected reaches the upstream. Despite `output_parse_pii: true`, the client
never receives the original values back, so on this profile's terms it is a **one-way
anonymiser: no leak, reversible-masking requirement not met** — the same class as
Presidio's own `replace`/`hash`/`mask` operators, and the same class as Portkey. The
client-visible stream also arrives as a single event, because the `output_parse_pii` path
assembles the response before re-emitting it.

**This row was previously withheld.** Against the old fixture it measured
`leaked: ["SSN"]` and would have been published as a `fail` — an accusation caused
entirely by the fixture's SSN being a value Presidio deliberately rejects. The fixture was
replaced rather than the row published.

## Latency: withdrawn, not caveated

This record previously carried a p50 latency table comparing LiteLLM+Presidio, Portkey Gateway
OSS and LLM-Shield-Proxy, and described LiteLLM+Presidio as the slowest configuration measured.
**All of it has been removed.**

The runner that produced those figures and its raw samples were not retained, so none of it can
be re-derived on demand, and a later independent re-measurement of one component of that work
found a materially different magnitude. Publishing a wrong speed claim about somebody else's
product is the same class of unretractable error as publishing a wrong leak finding, and this
project's entire credibility argument is that it does not make those.

Nothing here is a speed comparison. `client_observed_latency` in the artifacts above enforces no
threshold, gates on sample completeness, and runs three iterations; it is a completeness check
wearing a latency name. A performance claim would need a versioned runner committed to this
repository, run end to end against every gateway compared, with its raw output published beside
it — and none exists.

Secrets and synthetic fixture values are not written into any report. The API key, the
upstream key and extra-header values never appear in an artifact.
