# pii-leak-benchmark

**Does your LLM gateway send raw personal data to its upstream, and does it give the values back to the client?** This measures it, against any OpenAI-compatible `/v1` endpoint, in about a minute.

```bash
pip install pii-leak-benchmark
pii-leak-benchmark --target-base-url http://127.0.0.1:8899/v1
```

Standard library plus `httpx`. You should not have to install one gateway to measure another.

## A gateway check in your pull request

Use the [GitHub Action and regression guide](https://llmshieldproxy.com/docs/conformance/ci).
The Action starts your test gateway when given a startup command, checks the capture with
a negative control, and writes a job summary with results for each tested data type.
It saves reports even when the check fails. An optional baseline shows new failures and fixes.

```yaml
- uses: ninadphalak/LLM-Shield-Proxy@benchmark-v0.4.1
  with:
    target-base-url: http://127.0.0.1:4000/v1
    start-command: ./scripts/start-test-gateway.sh
    upstream-env: UPSTREAM_BASE_URL
```

Provide your gateway's startup script and upstream environment variable once. Run it in the
foreground and configure its redaction policy in your project. Choose `duty: anonymize` for
intentional one-way masking; the default is `restore`. `CHECK FAILED` identifies behavior
failures without an observed leak. `NOT MEASURED` identifies incomplete runs. Both fail CI.

Run the same check locally:

```bash
pip install "pii-leak-benchmark>=0.3.0"
pii-leak-benchmark ci --target-base-url http://127.0.0.1:4000/v1 --out pii-check
```

To pin the source rather than the package, install
`git+https://github.com/ninadphalak/LLM-Shield-Proxy@benchmark-v0.4.1#subdirectory=pii-leak-benchmark`.

The local command expects upstream routing to `http://127.0.0.1:8765/v1`. It writes a Markdown
summary, raw measurements and a versioned operator report. These smoke checks are separate
from the paper's response-injection and fragmentation experiments.

## What changed after the first benchmark run

All current results were produced by this project on one workstation. No outside contributor has repeated them yet. The results table marks each product result as `unreplicated` and links to the configuration and report for that run.

The first test prompt contained three invalid examples:

| Old test value | Why Presidio rejected it |
| :--- | :--- |
| `person@example.invalid` | `.invalid` is not a public domain suffix |
| `123-45-6789` | Presidio blocks this well-known invalid SSN sequence |
| `4532-1234-5678-9012` | The number fails the Luhn card-number checksum |

LLM-Shield-Proxy matched the text patterns but did not perform those validity checks. This gave it an unfair advantage over detectors that validate values. A LiteLLM and Presidio run revealed the problem: the old values produced `leaked: ["SSN"]`, while valid test values produced `leaked: []`. The project did not publish the affected result. It replaced the fixture with valid, reserved test values and reran all six configurations.

The benchmark also found two streaming bugs in LLM-Shield-Proxy. It created an OpenTelemetry span for every SSE event even when export was off, and it sent each event's blank terminator as a separate write. Both bugs are fixed and covered by regression tests.

**Known limitation:** the test uses three fixed data formats. A small program written specifically for those formats can pass without being a general PII detector. The values change on every run, but the formats do not. Testing more formats caused two false failures in six trials. The [fixture threat model](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/website/docs/conformance/fixture-threat-model.md) contains the measurements and the full change record.

## What it measures

You configure the gateway to use the benchmark's capture server as its model provider. The benchmark starts that server and sends a prompt containing valid but fictional personal data. The capture server checks the URL, HTTP method, headers, chunk extensions, trailers, and decoded JSON body for those values.

| Check | Fails when |
| :--- | :--- |
| `configured_upstream_boundary` | the gateway sends an unmasked test value to the capture server |
| `response_fidelity` | the client does not get the original values back |
| `sse_validity` | the response is not a valid SSE stream |
| `fragmentation_safety` | the client cannot rebuild the response from its SSE events |
| `client_observed_latency` | an iteration did not complete |

If the capture cannot safely inspect part of a request, such as an unsupported protocol or malformed header line, the run ends with an error instead of assuming that no value leaked. The repository records ten rounds of tests against bypass attempts.

## The v2 response-split profile

The package also ships `pii_leak_benchmark.v2_emitter`, which measures the **response** path
against the draft `spec/v2.0.0`. The checks above ask what the gateway sent upstream. The v2
profile asks what it sends back, and it asks two things that pull in opposite directions:

- the **echo** segment replays the prompt, so a masking gateway must put the caller's own
  values **back in** -- `FidelityRate`;
- the **injection** segment carries values that were never in the prompt, fragmented across
  SSE events, so the gateway must **take them out** -- `LeakRate`, and
  `DeltaFrag = LeakRate(adversarial) - LeakRate(single_chunk)`.

**No single global policy satisfies both.** Forward everything and the injection half fails;
redact everything and the echo half fails. `DeltaFrag` is the headline number because a
gateway can score perfectly when values arrive whole and still leak when the transport
splits them, and only the gap between the two shows it.

```bash
# the five reference policies, no containers, no credentials
python -m pii_leak_benchmark.v2_emitter --validate --out ./benchmark-output/v2 \
  --only passthrough,redact-all,chunk-local,bounded-retention,retention-plus-decoding

# a real gateway you are already running, configured to use the capture as its upstream
python -m pii_leak_benchmark.v2_emitter --validate --out ./benchmark-output/my-gateway \
  --only my-gateway --gateway-url http://127.0.0.1:4000/v1/chat/completions --upstream-port 8799
```

**Cut at every internal split point, not just the midpoint.** By default an adversarial case
cuts its value once, in the middle. That is one sample, and for a detector that scores a
fragment on what it looks like it is a weak one: `--exhaustive-splits` cuts at every internal
offset and fails the case if any split leaks. Measured against a live Presidio, it moved
`LeakRate(adversarial)` from 0.50 to 1.00. Use it before quoting a `DeltaFrag` from any
context-scored or validating detector.

`spec/v2.0.0` is a **draft** and is amended in place; `spec/v1.0.0` is frozen.

## A measurement is not a verdict

`passed` is the raw measurement. What a published row may *say* is a separate derived field, `outcome`, computed from the vendor's own claim (with a citation you supply) and the configuration you ran:

| `outcome` | Meaning |
| :--- | :--- |
| `pass` / `fail` | verdicts. `fail` means the gateway sent an unmasked test value to the capture server |
| `no-leak-profile-not-met` | non-pass with **no leak**; a one-way anonymizer that never restores values |
| `not-applicable` | the product does not claim PII redaction at all |
| `redaction-not-enabled` | it offers redaction; it was not turned on. A configuration statement |
| `inconclusive` | nothing correlated to your run; not attributable |
| `claim-unstated` | no claim recorded. The fail-closed default |

The harness calculates the outcome from the product's documented claim, the run configuration, and the measurements. A submitter cannot choose it. The report schema checks the calculation and rejects an inconsistent edit. Products that do not advertise PII redaction are marked `not-applicable`, not failed.

## Running it

The gateway under test must already be configured to send its upstream traffic to the capture (default `http://127.0.0.1:8765/v1`). The harness never reconfigures your gateway. A run that never reaches the capture reports `inconclusive`, not a leak, because the harness cannot distinguish "never configured" from "sent it somewhere else".

```bash
# The negative control: no gateway, raw pass-through. MUST report outcome=fail.
pii-leak-benchmark \
  --target-base-url capture://self \
  --target-name raw-pass-through-negative-control --target-version 1 \
  --redaction-claimed claimed \
  --redaction-claim-citation https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/website/docs/conformance/reproducing.md \
  --redaction-enabled \
  --redaction-config-reference "synthetic control: declared redaction intentionally absent"

# A real target, with the vendor's claim recorded
pii-leak-benchmark \
  --target-base-url http://127.0.0.1:8899/v1 \
  --target-name some-gateway --target-version 1.2.3 \
  --redaction-claimed claimed \
  --redaction-claim-citation https://vendor.example/docs/pii \
  --redaction-enabled --redaction-config-reference "guardrail: pii-redact" \
  --json-out ./result.json
```

Exit code is `0` when all checks passed, `1` when they did not, and `2` when the run itself could not be trusted. For example, it returns `2` if the capture was unreachable or something else was already listening on its port.

Hosted gateways are measurable too, by binding the capture behind your own tunnel and passing `--capture-public-url`. Put credentials in `CONFORMANCE_CAPTURE_TOKEN` and `CONFORMANCE_TARGET_API_KEY`; put credential-bearing extra headers in newline-delimited `CONFORMANCE_TARGET_HEADERS`. The corresponding flags remain available, but argv is visible in process listings. See the [hosted-gateway runbook](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/website/docs/conformance/hosted-gateway-runbook.md). This project does not operate a shared capture service; each operator controls the capture endpoint used for their run.

## Submitting a result

Every product result currently has one run from this project's maintainer. A result becomes `replicated` only after three different people each submit a run of the same gateway and configuration. Until then, the table marks it `unreplicated`. This includes LLM-Shield-Proxy.

A submission needs both the exact configuration and the JSON report produced by the run. See [submitting](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/website/docs/conformance/submitting.md).

To check a report before you send it, install `pii-leak-benchmark[validate]` and validate it against [`http-profile.schema.json`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/spec/v1.0.0/http-profile.schema.json). The schema is published in the repository rather than bundled here, so there is exactly one copy. It re-derives `outcome` in both directions, so a hand-edited report fails validation.

## Relationship to LLM-Shield-Proxy

This harness was extracted from [LLM-Shield-Proxy](https://github.com/ninadphalak/LLM-Shield-Proxy), which is one of the gateways in the results table. The proxy may import the benchmark, but the benchmark never imports the proxy. A test enforces that separation. Reports use the Streaming Privacy Gateway schemas in the repository's `spec/v1.0.0` directory.

Apache-2.0.
