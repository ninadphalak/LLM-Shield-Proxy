# Round-9 addendum: streaming mode regression probe

Measured 2026-09-13 on Windows 11 AMD64, CPython 3.14, loopback capture, Docker containers for
every target. Produced by `benchmarks/mode_regression_probe.py`.

## What this asks

The v2 profile always sends `stream: true`, so its "single-chunk" arm is streaming with the
protected value inside one event. That is a weaker baseline than the one a vendor tests against,
and it means the profile can report that an integration leaks but cannot report that an integration
**works until streaming is switched on**.

This addendum adds the missing arm. Three conditions, everything else held fixed:

| Condition | Request | Upstream response |
|---|---|---|
| `whole-response` | `stream: false` | one JSON body |
| `single-chunk` | `stream: true` | the value inside one SSE event |
| `adversarial-midpoint` | `stream: true` | the value cut at `len // 2` |

## What was found

**LiteLLM 1.99 with the Presidio guardrail, `mode: pre_call`, `output_parse_pii: true`.**

| Case | whole-response restored | streaming restored | new redaction tokens in streamed text | verdict |
|---|---|---|---|---|
| EMAIL, `sse-delta-content` | yes | no | CREDIT_CARD, EMAIL_ADDRESS, PHONE_NUMBER, URL | mode regression, restoration |
| EMAIL, `sse-json-field` | yes | no | CREDIT_CARD, PHONE_NUMBER, URL | mode regression, restoration |
| CARDPAN, `sse-delta-content` | yes | no | CREDIT_CARD, EMAIL_ADDRESS, PHONE_NUMBER, URL | mode regression, restoration |
| SSN, `sse-delta-content` | no | no | CREDIT_CARD, EMAIL_ADDRESS, PHONE_NUMBER, URL | **none**, see below |

In three of four cases the caller's own value came back intact when the response was returned whole
and did not come back when the same request was streamed. Request-path masking was identical in
every arm: `request_path_leaked_caller_value` is `false` throughout, and the upstream received the
same masked prompt either way. Only the response path differs.

The streamed text also acquired redaction tokens the capture never sent, and those tokens carry no
index suffix, while the request-side placeholders do. The observed client text for the EMAIL case
was:

```
whole-response : You sent: Please review: <the caller's own address>, ...
single-chunk   : You sent: Please review: <local part>@<URL>, <CREDIT_CARD>, <PHONE_NUMBER>
```

The local part of the caller's address survived and the domain did not. That is consistent with
restoration happening and then an undifferentiated response scan redacting the restored value a
second time, which is the ordering hazard the manuscript's engineering section already describes.
It is **consistent with**, not proof of: the mechanism has not been located in LiteLLM's source, and
this addendum makes no claim about it.

The apparent containment in the streaming arms is a side effect of that blanket redaction, not
evidence of a containment feature. The injected value is unrecoverable because everything of that
shape was redacted, including the caller's own data and the model's output.

### The SSN case is deliberately not counted

SSN did not restore in either mode, so it is a detector or placeholder coverage finding rather than
a mode regression, and the probe's derived verdict says so. The upstream received a mangled
placeholder run for that entity, which nothing downstream could have restored. Reporting it
alongside the other three would have been the exact error this project's outcome derivation exists
to prevent.

### NeMo Guardrails 0.24.0, streaming output rails, `context_size: 50`

No mode regression, because the behaviour is the same in all three arms.

| Condition | restored | injected value reached client | caller value reached upstream |
|---|---|---|---|
| whole-response | no | no | yes |
| single-chunk | no | no | yes |
| adversarial-midpoint | no | no | yes |

Two separate observations, and they point in opposite directions.

Response-path containment held in every arm, including the fragmented one, under the committed
`context_size: 50`. That is the behaviour a retention window is supposed to produce, and it is the
result the round-9 plan wanted to test against `context_size: 0`, which is the value NeMo's own
configuration validator recommends when a rewriting output rail is combined with streaming. That
comparison has not been run yet.

On the request path the caller's values reached the configured upstream unmasked in all three arms.
This configuration runs an output rail, not a request-path one, so that is in scope for what was
configured rather than a defect of the product. It is recorded because a reader comparing this row
with the LiteLLM rows needs to know the two targets were not asked to do the same job.

No echo value was restored in any arm. NeMo does not advertise restoration, so this is a
measurement and not a failure. Publishing it as a restoration defect would be the error that
`pii_leak_benchmark/redaction_claim.py` exists to prevent.

## What this is not

- **Not a conformance result.** These artifacts use `schema: mode-regression-probe/1`. They carry no
  `outcome`, no DeltaFrag, and validate against no published spec.
- **Not comparable with any v2 or FIDE report.** The probe has its own capture and client, and is
  deliberately outside `v2_emitter._INSTRUMENTED`, so no `inspector_sha256` applies to it. Round 8
  remains the authority for every published table, and its digest `94262e29a492ab6a` is unchanged by
  this work.
- **Not a product ranking.** Each row is one version, one configuration, one seed, one case.
- **Not a latency claim.** Delivery times are loopback against a synthetic upstream on one machine.
  They exclude model inference and wide area network time by construction.
- **Not a vendor defect report.** The measured behaviour may be intended, configurable, or fixed in
  a later version. No maintainer has been contacted about these runs as of this writing.

## Reproducing

```bash
# Control first. No gateway in the path, so everything should leak.
python benchmarks/mode_regression_probe.py --direct --upstream-port 8811 \
  --label control-no-gateway --out benchmarks/results/round9-addendum/control-direct.json

# One target, one case.
V2_GATEWAY_TOKEN=sk-v2-profile-local python benchmarks/mode_regression_probe.py \
  --gateway-url http://127.0.0.1:4321/v1/chat/completions \
  --model capture --upstream-port 8799 --entity EMAIL --carrier sse-delta-content \
  --label litellm-presidio --out <path>.json
```

Container recipes are in `benchmarks/litellm-v2-profile/STATUS.md` and
`benchmarks/nemo-v2-profile/STATUS.md`.

**One environment hazard cost real time here and is worth repeating**: the `presidio-anonymizer`
container reported `unhealthy` while still accepting TCP connections, and LiteLLM surfaced that as
`Presidio PII anonymization failed: TimeoutError` on every request. A TCP connect check is not a
health check. Confirm `curl http://127.0.0.1:5001/health` returns 200 before trusting any row.

## Files

| File | Contents |
|---|---|
| `control-direct.json` | No gateway. Every arm leaks, which is what passthrough means. |
| `litellm-presidio-*.json` | One file per entity and carrier. |
| `nemo-context-size-50.json` | NeMo Guardrails under the committed config. |
