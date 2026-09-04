# Portkey OSS gateway v2 profile run -- measured

**Status 2026-09-04: measured.** Results in `../results/v2-response-split/` --
`portkey-gateway-oss.json` and `seed-sweep-portkey.json`.

## Recipe

```bash
docker run -d --name portkey-v2 --network v2profile \
  --add-host=host.docker.internal:host-gateway -p 8788:8787 portkeyai/gateway:latest

export V2_GATEWAY_HEADERS='{"x-portkey-provider":"openai",
 "x-portkey-custom-host":"http://host.docker.internal:8799/v1",
 "x-portkey-config":"{\"output_guardrails\":[{\"checks\":[{\"id\":\"portkey.pii\",\"parameters\":{\"redact\":true}}]}]}"}'
V2_GATEWAY_TOKEN=sk-dummy python benchmarks/v2_seed_sweep.py --seeds 6 \
  --only portkey-gateway-oss \
  --gateway-url http://127.0.0.1:8788/v1/chat/completions \
  --upstream-port 8799 --model capture \
  --out benchmarks/results/v2-response-split/seed-sweep-portkey.json
```

Portkey cannot be addressed by URL alone: it routes on `x-portkey-provider` and
`x-portkey-custom-host`, and takes guardrail configuration from `x-portkey-config`. That is
what `V2_GATEWAY_HEADERS` exists for. It is deliberately strict about malformed JSON --
silently dropping the header that selects the guardrail would produce a passthrough run
labelled as a guarded one.

## The guardrail does nothing, and reports nothing

With and without the `x-portkey-config` header the response is **byte-identical**, HTTP 200
both times, no error surfaced. The gateway itself works fine as a streaming passthrough (4
SSE events, correctly relayed).

Reading the shipped bundle explains it. `/app/build/start-server.js` contains six `pii`
checks, under the namespaces `qualifire`, `portkey`, `patronus`, `pangea`, `promptfoo` and
`azure`. Every one of them is an HTTP call-out that reads `credentials.apiKey`; there is no
local detector in the OSS distribution. `executeHooks` wraps hook execution in a
`try/catch` that returns `{results: [], shouldDeny: false}`, so a hook that throws -- which
is what an unauthenticated call-out does -- **fails open silently**.

Scope: this describes `portkeyai/gateway:latest` run without third-party credentials, which
is the default a practitioner gets. It says nothing about Portkey's hosted product, and
nothing about the guardrail vendors, whose services were never contacted.

## Why this row matters even though it is a passthrough

Portkey scores FidelityRate 1.00, identical to LLM-Shield-Proxy -- for the opposite reason.
It never masked anything, so "the originals came back" is the absence of a transformation,
not a successful restoration. The capture's record of the prompt the upstream received is
the only thing in the profile that separates the two. See §1 of
`../results/v2-response-split/README.md`.

## What this run also found in the harness

Portkey pools upstream connections, which exposed a capture-side false pass: the harness
rebinds the capture to the same fixed port each case and used to leave the socket bound, so
a pooled connection got the previous case's response and the case scored as a non-leak.
Portkey initially measured LeakRate 0.33 instead of 1.00. Fixed; see finding 2.6 in the
results README and `tests/conformance/test_v2_capture_isolation.py`.
