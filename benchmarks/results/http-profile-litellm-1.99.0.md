# LiteLLM HTTP profile configuration and results

Two measured runs plus one diagnostic. Both runs were executed by the maintainer of
LLM-Shield-Proxy against a locally installed LiteLLM proxy; that is a conflict of interest and
is disclosed here rather than hidden. See [governance](../../website/docs/conformance/governance.md).

- Harness/source label: `fd3c5dd96cbd5db02583480b6ffead81fc1a6b6b` (clean tree)
- Target: `litellm[proxy]==1.99.0` under CPython **3.12.3**, `litellm --host 127.0.0.1 --port 4000`
- Controlled upstream: `http://127.0.0.1:8765/v1` (capture mode: `loopback`)
- Request iterations: 3
- Harness host: Windows 11, CPython 3.14.7, AMD64

`litellm==1.44.0` is not installable on this machine's CPython 3.14; the LiteLLM proxy was
installed into a separate CPython 3.12 virtualenv so that nothing in this repository's
environment was disturbed. Exact pins of the packages that affect the measured path:

```text
litellm==1.99.0
litellm-proxy-extras==0.4.89
litellm-enterprise==0.1.59
openai==2.54.0
fastapi==0.141.1
uvicorn==0.52.4
pydantic==2.13.5
```

`PYTHONIOENCODING=utf-8` is required on Windows: without it LiteLLM 1.99.0 aborts during ASGI
startup with `UnicodeEncodeError` while `click.echo`-ing its own startup banner under the
`cp1252` console codepage. `LITELLM_LOCAL_MODEL_COST_MAP=True` was set so the run makes no
outbound call for the model-cost map.

## Run 1 — default configuration, no PII masking

Artifact: `http-profile-litellm-1.99.0-default.json`
(SHA-256 `75465d9327848b7e843e3a3dd0b7dea2ef334cdea884c7f5152ba5f20dcba9ca`)

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

**Outcome: `redaction-not-enabled`.** This is a statement about the configuration, not a verdict
about LiteLLM. LiteLLM does not mask PII unless a guardrail is attached, and none was.

| Check | Result |
| :--- | :--- |
| `configured_upstream_boundary` | fail — `leaked_entity_types: [CREDIT_CARD, EMAIL, SSN]`, all three `literal` matches, `captured=3 correlated=3 uninspectable=0 marker_max=5` |
| `fragmentation_safety` | **pass** — 142 events, response reconstructed |
| `sse_validity` | **pass** — `status_codes: [200]` |
| `response_fidelity` | **pass** |
| `client_observed_latency` | **pass** — 3 of 3 iterations measured |

## Run 2 — Presidio PII masking guardrail enabled — NOT PUBLISHED AS A VERDICT

Artifact: `http-profile-litellm-1.99.0-presidio-NOT-A-VERDICT.json`
(SHA-256 `de94e49076eb406201416a3396a54c881cf4159adb0f3bdbd32e19e409256f0b`)

**The derived `outcome` in that artifact is `fail`. It must not be published as a LiteLLM
failure, and it is deliberately absent from the comparison table.** The cause is established
below: all three shipped fixture values are values Presidio is designed to reject, so its
detector never fires on them. A `fail` here would measure the fixture, not the product.

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

Measured: `leaked_entity_types: ["SSN"]` (`literal`), `captured=3 correlated=3 uninspectable=0
marker_max=5`; `response_fidelity` and `fragmentation_safety` fail; `sse_validity` and
`client_observed_latency` pass.

### Why the SSN leaked, established by direct measurement of Presidio

Every value in the shipped fixture is one a *validating* detector rejects. Queried directly
against the pinned analyzer, `score_threshold: 0.0`, English:

| Fixture value | Presidio result | Control value | Presidio result |
| :--- | :--- | :--- | :--- |
| `123-45-6789` | **no entity** — `US_SSN` invalidates the sequential digits | `456-78-9012` | `US_SSN`, score 0.85 |
| `4532-1234-5678-9012` | **no `CREDIT_CARD`** — Luhn checksum is 68, not a multiple of 10 | `4111-1111-1111-1111` | `CREDIT_CARD`, score 1.0 |
| `person@example.invalid` | **no `EMAIL_ADDRESS`** — reported as `URL`, score 0.5 | `bob@example.com` | `EMAIL_ADDRESS`, score 1.0 |

Analyzed as one string, the whole fixture prompt yields exactly two findings: `DATE_TIME`
over the card number and `URL` over part of the email. Those two incidental detections are why
only the SSN reached the capture — the email and the card were destroyed by a `URL` and a
`DATE_TIME` replacement, not by an email or card detector.

LiteLLM's own documentation example uses `4111-1111-1111-1111` and `test@example.com`, i.e.
a Luhn-valid card and a real TLD.

### The controlled experiment

`diagnostic-substituted-fixture-litellm-1.99.0-presidio.json` is the **same LiteLLM
configuration, same harness, same run**, with only `PROTECTED_VALUES` and the prompt template
monkey-patched in the driver to `bob.smith@example.com` / `456-78-9012` /
`4111-1111-1111-1111`. Result: `configured_upstream_boundary` **passes**, `leaked_entity_types:
[]`, `captured=3 correlated=3 marker_max=5`. `response_fidelity` and `fragmentation_safety`
still fail.

`diagnostic-substituted-fixture-llm-shield-proxy.json` is the same substituted fixture against
LLM-Shield-Proxy on the same host: 5/5 checks pass. So the substitution does not simply make
the profile easier.

**Neither diagnostic is a conformance result.** They do not use the shipped fixture, so they
are not comparable to any published row, and they are kept only as the evidence for the
finding above. Note that both nevertheless validate against
`spec/v1.0.0/http-profile.schema.json` — nothing in the schema binds a report to the shipped
fixture values.

### What LiteLLM + Presidio actually does, measured with detectable values

Observed directly at a logging upstream, one request, `output_parse_pii: true`:

```text
prompt sent by the client : ... contact bob.smith@example.com, SSN 456-78-9012, card 4111-1111-1111-1111
seen by the upstream      : ... contact <EMAIL_ADDRESS_1>SN <US_SSN_4>, card <CREDIT_CARD_5>
returned to the client    : ... contact <URL>ith@<URL>SN <US_SSN>, card <CREDIT_CARD>
```

Nothing protected reaches the upstream, and the client never receives the original values back.
On the profile's terms that is a one-way anonymizer: no leak, reversible-masking requirement not
met. The client-visible stream also arrives as a single event (`events_observed: 1`), because the
`output_parse_pii` path assembles the response before re-emitting it.

With a fixture Presidio can detect, the honest outcome for this configuration is therefore
`no-leak-profile-not-met` — the same class as Presidio's `replace`/`hash`/`mask` operators and
as Portkey, and **not** a leak finding. That row is not published either, because the run that
would carry it did not use the shipped fixture.

## Latency observations — NOT a comparison

`client_observed_latency` enforces no threshold; it gates on sample completeness. Three
iterations is a smoke test, the runs were serialised on one loaded developer workstation, and
the capture upstream echoes one SSE event per character, so these numbers are recorded for
completeness and **must not be presented comparatively**. Raise the iteration count and run the
targets under equal conditions before drawing any conclusion.

| Run | mean ms | p50 ms |
| :--- | ---: | ---: |
| Raw capture negative control | 41.6 | 38.1 |
| Portkey Gateway OSS, default | 58.0 | 53.9 |
| Portkey Gateway OSS + `regexReplace` | 51.9 | 51.9 |
| LLM-Shield-Proxy (committed self-test) | 122.9 | 116.9 |
| LiteLLM 1.99.0, default | 876.0 | 130.4 |
| LiteLLM 1.99.0 + Presidio | 925.8 | 171.7 |

The mean/p50 gap on the LiteLLM rows is a first-request effect: the first iteration pays
LiteLLM's lazy initialisation. On these samples Portkey OSS is the fastest gateway measured and
LLM-Shield-Proxy is not; that is recorded rather than omitted, and it is not evidence of
anything at n=3.

Secrets and synthetic fixture values are not written into any report. The API key, the upstream
key and extra-header values never appear in an artifact.
