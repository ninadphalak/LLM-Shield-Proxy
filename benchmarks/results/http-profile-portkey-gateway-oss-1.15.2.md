# Portkey Gateway (OSS, self-hosted) HTTP profile configuration and results

Two measured runs. Executed by the maintainer of LLM-Shield-Proxy against a locally run
Portkey Gateway; that is a conflict of interest and is disclosed rather than hidden. See
[governance](../../website/docs/conformance/governance.md).

This is the **open-source, self-hosted Portkey Gateway**, not Portkey's hosted platform. No
Portkey account, API key, or dashboard configuration was used or needed.

- Harness/source label: `fd3c5dd96cbd5db02583480b6ffead81fc1a6b6b` (clean tree)
- Target: Portkey Gateway **1.15.2**, from the vendor image
  `portkeyai/gateway@sha256:97f094d9c8a764cbfaa2a7138c0017b247ca923bb06db1b4c13b7f8a33b5200d`
- Controlled upstream: `http://127.0.0.1:8765/v1` (capture mode: `loopback`)
- Request iterations: 3
- Harness host: Windows 11, CPython 3.14.7, AMD64

## How the target was run, and why it matters

The gateway listens on `127.0.0.1:8787`. It is pointed at the capture with the per-request
header `x-portkey-custom-host`, so no dashboard or config file is involved:

```text
--target-base-url http://127.0.0.1:8787/v1
--target-header x-portkey-provider=openai
--target-header x-portkey-custom-host=http://127.0.0.1:8765/v1
```

The gateway's SSRF guard blocks `.local`, `.internal`, `.localdomain`, `.lan`, `.corp`,
`.test`, `.invalid`, `.onion` and private ranges, but ships a default trusted set of
`localhost`, `127.0.0.1`, `::1`, `host.docker.internal` (overridable with
`TRUSTED_CUSTOM_HOSTS`). A loopback capture is therefore reachable, which is why these runs
are `loopback` mode rather than the weaker `public` mode the hosted product would require.

**The image's own `node_modules` tree was used, not a fresh `npm install`.** `npm install
@portkey-ai/gateway@1.15.2` produces a tree whose transitive dependencies have drifted
(`hono` 4.13.5 / `@hono/node-server` 1.19.17 versus 4.9.7 / 1.13.5 in the image), and on that
tree **every streaming request fails** with `TypeError: immutable` inside the gateway's own
`updateHeaders`, raised from `undici`'s `Headers.append` on an immutable response. Non-streaming
requests succeed, so the failure is invisible until you stream. Pinning `hono` and
`@hono/node-server` back was not sufficient; the same request succeeded unchanged inside the
official container. The vendor tree was therefore extracted with `docker cp` and executed on
the host under Node **20.19.6** (the image's Node version), which reproduces the container's
behaviour while keeping the capture on loopback.

**This is a reproducibility finding about how to pin the target, not a defect finding about
Portkey.** Anyone measuring Portkey OSS must pin the shipped artifact, not the npm package
name, or they will record a 500 that the vendor's own build does not produce.

## Run 1 — default configuration, no guardrails

Artifact: `http-profile-portkey-gateway-oss-1.15.2-default.json`
(SHA-256 `acd79ad14b76ff6f9c2f6aa1d8659abf351394cb88beb9edae46a3e8ef8196e8`)

No `x-portkey-config`, no hooks.

Redaction claim recorded: `claimed`, **not** configured for this run, cited to
<https://portkey.ai/docs/product/guardrails/pii-redaction>.

**Outcome: `redaction-not-enabled`.** A configuration statement, not a verdict.

| Check | Result |
| :--- | :--- |
| `configured_upstream_boundary` | fail — `leaked_entity_types: [CREDIT_CARD, EMAIL, SSN]`, all `literal`, `captured=3 correlated=3 uninspectable=0 marker_max=5` |
| `fragmentation_safety` | **pass** — 138 events, response reconstructed |
| `sse_validity` | **pass** — `status_codes: [200]` |
| `response_fidelity` | **pass** |
| `client_observed_latency` | **pass** — 3 of 3 iterations measured |

## Run 2 — redaction enabled via `default.regexReplace`

Artifact: `http-profile-portkey-gateway-oss-1.15.2-regexreplace.json`
(SHA-256 `b72a949c8fcf3a5f5b858f8c90c16a668c1848bacac8668d48e84bf8c809f19f`)

Sent as the `x-portkey-config` request header:

```json
{
  "before_request_hooks": [
    {
      "type": "guardrail",
      "id": "conformance-pii-redact",
      "deny": false,
      "checks": [
        {
          "id": "default.regexReplace",
          "parameters": {
            "rule": "/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\b(?:\d{4}[- ]?){3}\d{4}\b|\b\d{3}-\d{2}-\d{4}\b/g",
            "redactText": "[REDACTED]"
          }
        }
      ]
    }
  ]
}
```

**What this measures, and what it does not.** `default.regexReplace` is the only redaction
plugin in the OSS gateway that needs no vendor credentials. The patterns above are
**tester-authored**. This run therefore measures Portkey's gateway and its guardrail transform
engine; it does **not** measure Portkey's own PII detection quality. The gateway's PII
guardrail (`portkey.pii`, and every other detector-backed plugin — `azure.pii`,
`promptfoo.pii`, `patronus.phi`, `pangea.textGuard`, …) calls out to a vendor API and cannot be
exercised without an account.

Two behaviours were found while configuring this and are recorded so the row reproduces:

- `default.regexMatch` — the check Portkey's hosted "Regex Match" guardrail is built on —
  returns only a verdict in this build. It performs no redaction. `default.regexReplace` is the
  transforming plugin.
- **Multiple `regexReplace` transforms do not compose in one request.** With three checks in one
  hook, or three separate hooks, only the last transform survived: each is computed against the
  original request and the results overwrite one another. A single alternation pattern is
  required to redact more than one entity type. Both shapes were reproduced at a logging
  upstream before settling on the alternation.

Redaction claim recorded: `claimed`, configured for this run, cited as above, with the
tester-authored caveat carried in `redaction_claim.configuration_reference` inside the artifact
itself.

**Outcome: `no-leak-profile-not-met`. This is not a leak finding and must not be published as
one.**

| Check | Result |
| :--- | :--- |
| `configured_upstream_boundary` | **pass** — `leaked_entity_types: []`, `leak_evidence: []`, `captured=3 correlated=3 uninspectable=0 marker_max=5` |
| `sse_validity` | **pass** — `status_codes: [200]` |
| `client_observed_latency` | **pass** — 3 of 3 iterations measured |
| `response_fidelity` | fail — `[REDACTED]` is one-way; the original value is never restored |
| `fragmentation_safety` | fail — gates on the same reconstruction. Streaming itself was fine: `events_observed: 117` |

Publish this as "no leak; does not meet the reversible-masking requirement". Portkey redacted
everything it was asked to redact and sent nothing protected to the upstream. The profile
requires *reversible* masking, and one-way replacement is a different design, not a privacy
failure. This independently reproduces the prediction recorded before any Portkey run existed.

## Latency observations — NOT from this profile

`client_observed_latency` in the artifacts above enforces no threshold and three
iterations is a smoke test. A separate maintainer-local diagnostic recorded 150
iterations per point, 25 discarded as warmup, every gateway pointed at the same fixed
upstream, one request at a time to completion of the stream, on one loaded developer
workstation. Portkey is running here as a local Node process on Windows from the vendor
image's own tree; this is not a tuned deployment and the numbers should not be read as
one.

The diagnostic runner and raw samples were not retained, so the exact figures below are
not independently auditable and must not be cited as benchmark evidence. Re-measure with
a versioned runner and raw artifact before making a performance claim.

Token-by-token upstream, p50 milliseconds:

| Target | 200 events | 800 events | per event |
| :--- | ---: | ---: | ---: |
| Upstream directly (floor) | 0.77 | 0.77 | ~0 |
| Minimal Starlette+httpx passthrough | 4.12 | 4.02 | ~0 |
| LLM-Shield-Proxy | 12.69 | 24.55 | 0.020 ms |
| Portkey Gateway OSS (no guardrail) | 47.00 | 47.27 | 0.0004 ms |
| Portkey Gateway OSS + `regexReplace` | 46.95 | 47.47 | 0.0007 ms |

The two gateways have opposite shapes. Portkey's cost is almost entirely **fixed** — about
47 ms per request regardless of response length, and its `regexReplace` transform is free
within measurement noise. LLM-Shield-Proxy's fixed cost is far lower and its cost grows
with the number of SSE events.

**An earlier three-iteration measurement showed Portkey at p50 52 ms against this proxy's
117 ms, and that comparison was published as "not comparable at n=3". Investigating it
properly found two real defects in this proxy**, both in the SSE response path: one
OpenTelemetry span per SSE *delta* (11% of request-path CPU, and unusable telemetry — a
500-token answer emitted 500 spans), and two ASGI writes per SSE event because the
pipeline yielded each line separately, the second write being one byte. Before the fix the
proxy cost **0.396 ms per event**; after, **0.020 ms**. The numbers above are post-fix.
`tests/test_streaming_write_efficiency.py` pins both, and reverting either fails it.

Extrapolating the two fits, they cross at roughly 1,400 SSE events: below that this proxy
is faster because its fixed cost is far lower, above it Portkey is faster because its
per-event cost is. A typical chat completion is well under 1,400 tokens, but a long
document generation is not. Neither result is a general claim about either product, and
the crossover is an extrapolation, not a measurement.

Secrets and synthetic fixture values are not written into any report. Extra-header values are
never recorded; only header names are.
