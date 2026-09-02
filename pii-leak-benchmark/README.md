# pii-leak-benchmark

**Does your LLM gateway send raw personal data to its upstream, and does it give the values back to the client?** This measures it, against any OpenAI-compatible `/v1` endpoint, in about a minute.

```bash
pip install pii-leak-benchmark
pii-leak-benchmark --target-base-url http://127.0.0.1:8899/v1
```

Standard library plus `httpx`. Nothing else, ever — you should not have to install one gateway to measure another.

## The first thing it found was that it was rigged in its author's favour

This tool was written by the author of a privacy gateway, which is a conflict of interest, so the interesting evidence is what it has said about him.

The prompt it sends used to carry three fixed values, chosen to be safe to publish: `person@example.invalid`, `123-45-6789`, `4532-1234-5678-9012`. Every one of them is a value a *validating* detector is built to reject — `.invalid` is not a real public suffix, that SSN is a blacklisted sequence, and the card fails its Luhn checksum (sum 68). Measured against a pinned Presidio at `score_threshold: 0.0`: no `EMAIL_ADDRESS`, no `US_SSN`, no `CREDIT_CARD`. Not low confidence — nothing at all.

The author's own engine used bare regexes with no checksum and no range check, so it caught all three. **The benchmark was scoring a careful detector worse than a careless one, in the direction that flattered its author.** It was found by running the harness against LiteLLM+Presidio, which reported `leaked: ["SSN"]` on the shipped fixture and `leaked: []` on the same run with valid specimens substituted. That row was withheld rather than published, the fixture was replaced with values that are both valid specimens and drawn from reserved space, and all six rows were re-run.

The next thing it found was two defects in the author's own gateway's streaming hot path: an OpenTelemetry span opened per SSE delta even with export disabled, and the data line and its terminating blank line yielded as separate writes.

Both stories are published in full, including [the fixture's remaining weakness](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/website/docs/conformance/fixture-threat-model.md): a ~35-line `str.replace` shim with no detector in it passes all five checks. That is measured, unfixed and deliberate — randomising the fixture cost a one-in-three false-accusation rate against a correctly-redacting gateway, which is worse than the defect. Bind the artifact to the run instead; that work is open.

## What it measures

The harness stands a controlled capture server in front of the gateway's configured upstream, sends a prompt containing synthetic-but-valid personal data, and inspects **every channel** the gateway could reach the capture through — request line, method, headers, chunk extensions, trailers and the JSON body, decoded and re-walked — for those values.

| Check | Fails when |
| :--- | :--- |
| `configured_upstream_boundary` | a protected value reaches the capture in any channel |
| `response_fidelity` | the client does not get the original values back |
| `sse_validity` | the response is not a valid SSE stream |
| `fragmentation_safety` | the stream is not reconstructible across events |
| `client_observed_latency` | an iteration did not complete |

Anything the capture cannot inspect — an unsupported protocol, a malformed header line, a budget exceeded — **fails closed**. Ten adversarial rounds are recorded in the repository; the rule that survived them is *enumerate the channel, not the encoding*.

## A measurement is not a verdict

`passed` is the raw measurement. What a published row may *say* is a separate derived field, `outcome`, computed from the vendor's own claim (with a citation you supply) and the configuration you ran:

| `outcome` | Meaning |
| :--- | :--- |
| `pass` / `fail` | verdicts. **`fail` means one thing: protected data reached the capture** |
| `no-leak-profile-not-met` | non-pass with **no leak** — a one-way anonymizer that never restores values |
| `not-applicable` | the product does not claim PII redaction at all |
| `redaction-not-enabled` | it offers redaction; it was not turned on. A configuration statement |
| `inconclusive` | nothing correlated to your run; not attributable |
| `claim-unstated` | no claim recorded. The fail-closed default |

You cannot type the outcome: it is derived, and the published schema re-derives it in both directions, so a hand-edited report fails validation. This exists because the harness can measure products it has no business judging — printing "Fail" for a caching gateway that never claimed to redact anything is an accusation a referee cannot retract.

## Running it

The gateway under test must already be configured to send its upstream traffic to the capture (default `http://127.0.0.1:8765/v1`) — the harness never reconfigures your gateway for you. A run that never reaches the capture reports `inconclusive`, not a leak: the harness cannot tell "never configured" from "sent it somewhere else", and says so rather than implying the worse one.

```bash
# The negative control: no gateway, raw pass-through. MUST report outcome=fail.
pii-leak-benchmark --target-base-url capture://self

# A real target, with the vendor's claim recorded
pii-leak-benchmark \
  --target-base-url http://127.0.0.1:8899/v1 \
  --target-name some-gateway --target-version 1.2.3 \
  --redaction-claimed claimed \
  --redaction-claim-citation https://vendor.example/docs/pii \
  --redaction-enabled --redaction-config-reference "guardrail: pii-redact" \
  --json-out ./result.json
```

Exit code is `0` when all checks passed, `1` when they did not, `2` when the run itself could not be trusted (the capture was unreachable, or something else was already listening on its port — the harness probes its own capture before sending any target traffic and aborts rather than reporting a leak it cannot attribute).

Hosted gateways are measurable too, by binding the capture behind your own tunnel and passing `--capture-public-url` with a `--capture-token` (env `CONFORMANCE_CAPTURE_TOKEN`; argv is visible in process listings). See the [hosted-gateway runbook](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/website/docs/conformance/hosted-gateway-runbook.md). This project does not, and will not, operate a capture service for you: hosting it would be both a burden and a conflict of interest.

## Submitting a result

The [results table](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/website/docs/conformance/results.md) records, per gateway, how many independent runs exist and **how many distinct people** produced them. Below three runs from three distinct submitters a gateway reads `unreplicated`, not a verdict — including the reference implementation's own row, which is 1 run by 1 submitter and labelled as such.

A submission needs the pinned configuration **and** the raw artifact, never one without the other. See [submitting](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/website/docs/conformance/submitting.md).

To check a report before you send it, install `pii-leak-benchmark[validate]` and validate it against [`http-profile.schema.json`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/spec/v1.0.0/http-profile.schema.json) — the schema is published in the repository rather than bundled here, so there is exactly one copy of it and it cannot drift from the specification. It re-derives `outcome` in both directions, so a hand-edited report fails validation.

## Relationship to LLM-Shield-Proxy

This harness was extracted from [LLM-Shield-Proxy](https://github.com/ninadphalak/LLM-Shield-Proxy), which is one of the gateways it scores and is labelled in the table as the reference implementation. The dependency runs one way only — the proxy may use the benchmark, the benchmark never imports the proxy, and a test enforces it. Reports validate against that repository's `spec/v1.0.0` Streaming Privacy Gateway schemas, which keep the SPG name.

Apache-2.0.
