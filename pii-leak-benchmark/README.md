# pii-leak-benchmark

**Does your LLM gateway send raw personal data to its upstream, and does it give the values back to the client?** This measures it, against any OpenAI-compatible `/v1` endpoint, in about a minute.

```bash
pip install pii-leak-benchmark
pii-leak-benchmark --target-base-url http://127.0.0.1:8899/v1
```

Standard library plus `httpx`. You should not have to install one gateway to measure another.

## The first result exposed a biased fixture

This benchmark and LLM-Shield-Proxy share a maintainer. That is a conflict of interest, so every self-run result is labelled `unreplicated` and published with its configuration and raw report for other people to check.

The prompt used to carry three fixed values chosen to be safe to publish: `person@example.invalid`, `123-45-6789`, and `4532-1234-5678-9012`. A validating detector rejects all three: `.invalid` is not a real public suffix, that SSN is a blacklisted sequence, and the card fails its Luhn checksum. A pinned Presidio at `score_threshold: 0.0` returned no `EMAIL_ADDRESS`, `US_SSN`, or `CREDIT_CARD` finding for them.

This project's engine used regexes without those validation checks, so it caught all three. The fixture therefore favored this project's detector design. A run against LiteLLM+Presidio exposed the problem: it reported `leaked: ["SSN"]` with the old fixture and `leaked: []` when valid specimens were substituted. That row was withheld, the fixture was replaced with valid specimens drawn from reserved space, and all six rows were rerun.

The benchmark also exposed two defects in LLM-Shield-Proxy's streaming hot path: an OpenTelemetry span opened for every SSE delta even with export disabled, and the data line and its terminating blank line were emitted as separate writes. Both are fixed.

The [fixture threat model](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/website/docs/conformance/fixture-threat-model.md) also documents a remaining limitation: a roughly 35-line `str.replace` shim with no general detector can pass all five checks. The formats remain fixed because broader format randomization produced false leak findings in two of six variants against a correctly redacting gateway. Values now vary within those formats. Submitted CI runs can also use detached GitHub/Sigstore provenance over the finished report bytes. That prevents a hand-edited report from posing as workflow output, but it cannot prove that the measured process was the claimed target image.

## What it measures

The harness stands a controlled capture server in front of the gateway's configured upstream, sends a prompt containing synthetic-but-valid personal data, and inspects **every channel** the gateway could use to reach the capture: request line, method, headers, chunk extensions, trailers, and the decoded JSON body.

| Check | Fails when |
| :--- | :--- |
| `configured_upstream_boundary` | a protected value reaches the capture in any channel |
| `response_fidelity` | the client does not get the original values back |
| `sse_validity` | the response is not a valid SSE stream |
| `fragmentation_safety` | the stream is not reconstructible across events |
| `client_observed_latency` | an iteration did not complete |

Anything the capture cannot inspect, such as an unsupported protocol or malformed header line, **fails closed**. Ten adversarial rounds are recorded in the repository; the rule that survived them is *enumerate the channel, not the encoding*.

## A measurement is not a verdict

`passed` is the raw measurement. What a published row may *say* is a separate derived field, `outcome`, computed from the vendor's own claim (with a citation you supply) and the configuration you ran:

| `outcome` | Meaning |
| :--- | :--- |
| `pass` / `fail` | verdicts. **`fail` means one thing: protected data reached the capture** |
| `no-leak-profile-not-met` | non-pass with **no leak**; a one-way anonymizer that never restores values |
| `not-applicable` | the product does not claim PII redaction at all |
| `redaction-not-enabled` | it offers redaction; it was not turned on. A configuration statement |
| `inconclusive` | nothing correlated to your run; not attributable |
| `claim-unstated` | no claim recorded. The fail-closed default |

You cannot type the outcome: it is derived, and the published schema re-derives it in both directions, so a hand-edited report fails validation. This avoids calling a caching or routing gateway a privacy failure when it never claimed to redact PII.

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

Hosted gateways are measurable too, by binding the capture behind your own tunnel and passing `--capture-public-url` with a `--capture-token` (env `CONFORMANCE_CAPTURE_TOKEN`; argv is visible in process listings). See the [hosted-gateway runbook](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/website/docs/conformance/hosted-gateway-runbook.md). This project does not, and will not, operate a capture service for you: hosting it would be both a burden and a conflict of interest.

## Submitting a result

The [results table](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/website/docs/conformance/results.md) records, per gateway, how many independent runs exist and **how many distinct people** produced them. Below three runs from three distinct submitters, a gateway reads `unreplicated`, not a verdict. This includes the reference implementation's own row, which is 1 run by 1 submitter.

A submission needs the pinned configuration **and** the raw artifact, never one without the other. See [submitting](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/website/docs/conformance/submitting.md).

To check a report before you send it, install `pii-leak-benchmark[validate]` and validate it against [`http-profile.schema.json`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/spec/v1.0.0/http-profile.schema.json). The schema is published in the repository rather than bundled here, so there is exactly one copy. It re-derives `outcome` in both directions, so a hand-edited report fails validation.

## Relationship to LLM-Shield-Proxy

This harness was extracted from [LLM-Shield-Proxy](https://github.com/ninadphalak/LLM-Shield-Proxy), which is one of the gateways it scores and is labelled in the table as the reference implementation. The dependency runs one way only: the proxy may use the benchmark, the benchmark never imports the proxy, and a test enforces it. Reports validate against that repository's `spec/v1.0.0` Streaming Privacy Gateway schemas, which keep the SPG name.

Apache-2.0.
