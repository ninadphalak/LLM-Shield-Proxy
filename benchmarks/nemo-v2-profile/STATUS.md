# NeMo Guardrails v2 profile run -- measured, with three cases refused

**Status 2026-09-04: measured.** Results in `../results/v2-response-split/` --
`nemo-guardrails-0.24.0.json` and `seed-sweep-nemo.json`.

`nemoguardrails` 0.24.0, its own Presidio running in-process. This row exists because the
other three gateways leave a hole: **none of them has a local response-side PII detector.**
LiteLLM calls a Presidio service and buffers the whole response, LLM-Shield-Proxy has no
response detector at all, and every Portkey PII check is a credentialed call-out. NeMo is
the only one of the four that inspects the response with a detector in its own process.

## Result

| Fidelity | Leak (1-chunk) | Leak (adv) | DeltaFrag | Echo observable | Inconclusive | Outcome |
|---:|---:|---:|---:|---:|---:|---|
| 0.00 | 0.00 | 0.00 | 0.00 | 3 of 12 | **3 of 12** | `no-leak-profile-not-met` |

Stable across 6 seeds. `delivery_confirmed: false`, so the injection-containment check does
**not** pass: three cases produced no client response at all, and a case the client never
saw cannot testify to containment.

NeMo does not mask the request, so nothing was ever masked to restore. Its 0.00 fidelity is
"the response was truncated", not "restoration failed" -- read it beside
`capture.upstream_bodies`, which shows the caller's values reaching the upstream intact.

## Three findings that do not need the numbers

**1. `mask sensitive data on output` is unusable in 0.24.0.** Every request dies with
`mask_sensitive_data() got an unexpected keyword argument 'context'`. The action dispatcher
passes `context=` to every action; the two sibling actions do not agree on whether to accept
it. Verified with `inspect.signature` against the installed package, not inferred from the
error text:

```
mask_sensitive_data  (source, text, config)            -> RailOutcome     # no **kwargs
detect_sensitive_data(source, text, config, **kwargs)  -> RailOutcome
```

So the **detect** rail runs and the **mask** rail cannot. This profile therefore measures
`detect sensitive data on output`, which blocks rather than redacts. That is a different
policy from the one a practitioner reaching for "mask" would expect, and the substitution is
declared here rather than folded into the row.

**2. A response guardrail turns streaming off by default.** With output rails configured and
`rails.output.streaming.enabled` unset, NeMo answers HTTP 400:

> `stream_async() cannot be used when output rails are configured but
> rails.output.streaming.enabled is False`

A deliberate refusal, not a bug -- and worth recording, because the default posture when a
response guardrail is present is to stop streaming.

**3. A rewriting rail is not allowed a retention window at all.** Configuring the mask rail
with streaming makes the config validator refuse to start:

> `Output rails ['mask sensitive data on output'] rewrite the response, which streaming
> cannot apply with stream_first: True and context_size: 50: set
> rails.output.streaming.stream_first to False and context_size to 0, or remove the
> rewriting rails from a streaming configuration.`

**`context_size: 0` is no retention across chunk boundaries** -- the `chunk-local` policy,
the one this profile measures leaking under fragmentation. The product's own validator
forces a masking rail into that configuration. This is the paper's central trade-off
appearing as a vendor's config constraint rather than as a measurement, and it is the
cleanest external evidence in the repository that the trade-off is real. The non-rewriting
`detect` rail is not subject to it and keeps the default `context_size: 50`.

## Three cases refused, and why that is a row rather than a crash

NeMo answers **HTTP 422** to a request carrying the `tool-description` site. Those three
cases are recorded `inconclusive`: nothing was measured, and scoring them 0 would credit the
gateway with a clean result it never earned -- a gateway could otherwise improve its LeakRate
by rejecting the cases it cannot handle. `metrics.cases_inconclusive` is 3, and the v2
schema already forbids `passed: true` when it is non-zero.

Note the contrast with Portkey, which **silently drops** an unrecognised top-level key and
returns 200. Refusing loudly and dropping silently are different behaviours and the profile
distinguishes them: refusal is `inconclusive`, silent drop is `echo_observable: 0`.

## Recipe

```bash
docker build -f benchmarks/nemo-v2-profile/Dockerfile -t nemo-guardrails:v2 benchmarks/nemo-v2-profile

# --default-config-id is required: without it every request is 422 with
# "No guardrails config_id provided and server has no default configuration".
MSYS_NO_PATHCONV=1 docker run -d --name nemo-v2 --network v2profile \
  --add-host=host.docker.internal:host-gateway -p 9001:9000 \
  -e OPENAI_API_KEY=sk-dummy-not-used \
  -v "C:/git_repo/LLM-Shield-Proxy/benchmarks/nemo-v2-profile/config:/config:ro" \
  --entrypoint nemoguardrails nemo-guardrails:v2 \
  server --config /config --port 9000 --default-config-id config

python benchmarks/v2_seed_sweep.py --seeds 6 --only nemo-guardrails-0.24.0 \
  --gateway-url http://127.0.0.1:9001/v1/chat/completions \
  --upstream-port 8799 --model config \
  --out benchmarks/results/v2-response-split/seed-sweep-nemo.json
```

The `[server]` extra is required and is not implied by the base install; without it the
entrypoint exits with "Server dependencies are missing".

## What this run cost the harness, and what it fixed

Three harness defects surfaced here, all now fixed and all in the same family -- the
instrument reporting something other than what it measured:

- **The capture answered a bad case with a dead socket.** A missing axis key raised inside
  the handler, which closed the connection without writing anything; the client reported
  `Server disconnected without sending a response`, a transport error for what was a harness
  bug. It sent an hour of debugging at NeMo. The capture now answers 500 with the reason.
- **`_haystacks` crashed on any SSE event that was not a standard delta**, and skipping such
  an event would have been a false pass: those bytes reached the client. It now scans the raw
  event.
- **One refused case aborted the whole run.** Now recorded as inconclusive.
