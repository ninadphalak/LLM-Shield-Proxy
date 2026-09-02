# Reproduce the Conformance Report

## From a source checkout

```bash
python -m pip install -e ./pii-leak-benchmark -e ".[dev]"
llm-shield-proxy benchmark \
  --iterations 10000 \
  --json-out CONFORMANCE_LATEST.json
```

The **local** profile measures this project's own engines in process, so it needs the
gateway installed. **The HTTP profile does not, and is no longer part of this package at
all.** It ships as its own distribution, `pii-leak-benchmark` - standard library plus
`httpx`, with no gateway dependency - so measuring one gateway does not require installing
another:

```bash
pip install pii-leak-benchmark
pii-leak-benchmark --target-base-url http://127.0.0.1:8000/v1
```

The runner performs no public-model call and writes no test PII into the report. Set the exact revision explicitly when running outside GitHub Actions:

```bash
LLM_SHIELD_SOURCE_REVISION=$(git rev-parse HEAD) \
  llm-shield-proxy benchmark --iterations 10000 --json-out CONFORMANCE_LATEST.json
```

On PowerShell:

```powershell
$env:LLM_SHIELD_SOURCE_REVISION = git rev-parse HEAD
py -m llm_shield_proxy.cli benchmark --iterations 10000 --json-out CONFORMANCE_LATEST.json
```

## Verify the artifact

Confirm that:

1. `schema` ends in `/v1.0.0`;
2. `source_revision` equals the revision tested;
3. all six `checks` are present and pass;
4. protected vector values are absent;
5. timing scope excludes components not exercised;
6. memory scope distinguishes Python allocations from process RSS.

There is deliberately no `latency_measurement` check. It gated on percentiles of
monotonic-clock deltas being non-negative, which cannot fail under any implementation
or any input, and a check that cannot fail is not evidence — it inflated the check
count without adding a bit of information. The timings are still published under
`microbenchmarks`, as labeled measurements rather than as a passed gate.

Use `benchmarks/REPORTING.md` for a production-shaped comparison. Publish unsuccessful runs and deviations alongside successful results.

## Run the OpenAI-compatible HTTP profile

For a hosted target behind a vendor account, follow the
[hosted-gateway runbook](./hosted-gateway-runbook) instead: it carries the per-vendor
configuration and the rule governing what a published row may say.

The profile drives the gateway under test over HTTP and watches what that gateway sends to
its **configured upstream**, which for the duration of the run is a capture server the
harness starts. Two deployment modes decide how strong the resulting observation is.

### Capture mode: loopback

The default. The capture binds `127.0.0.1`, so only a gateway you run yourself — a local
process, a container, a pod you can route out of — can reach it. This is the **stronger**
observation: the address is unreachable from outside, so every request that arrives is the
target's.

```text
http://127.0.0.1:8765/v1              # gateway runs on the host
```

```bash
CONFORMANCE_TARGET_API_KEY=local-evaluation-key \
pii-leak-benchmark \
  --target-base-url http://127.0.0.1:8000/v1 \
  --target-name implementation-under-test \
  --target-version pinned-version \
  --iterations 10 \
  --json-out HTTP_CONFORMANCE.json
```

The command starts the controlled capture upstream on `127.0.0.1:8765`, sends the
synthetic request through the target, and stops the capture afterward. The report never
includes the API key, extra header values, or protected fixture values.

A gateway in a container is still a loopback-shaped case as far as trust goes, but it
needs an address it can route to. Bind a reachable interface and name that address
explicitly — which puts the run in public mode, with the token requirement that implies:

```python
capture_host="0.0.0.0",
capture_public_url="http://host.docker.internal:8765/v1",   # common Docker Desktop route
capture_token="a-long-random-value",
```

Restrict access to that port with the host firewall.

### Capture mode: public

A hosted gateway cannot open a connection to your loopback. This project does not operate a
shared capture service; instead, **you** deploy the capture on your own VPS or behind a tunnel
you already run (`cloudflared`, `ngrok`, or equivalent). This keeps the endpoint and its logs
under the operator's control.

```python
from pii_leak_benchmark import run_http_conformance

report = run_http_conformance(
    "https://the-gateway-under-test.example/v1",
    api_key="...",
    iterations=10,
    capture_host="0.0.0.0",                                   # reachable interface
    capture_port=8765,
    capture_token="a-long-random-value",                      # required in this mode
    capture_public_url="https://your-tunnel.example/v1",      # what the target will use
)
```

Three things are required and enforced:

- **`capture_host` beyond loopback** binds a reachable interface.
- **`capture_public_url`** is the externally reachable base URL the target will actually
  be configured with. It is required whenever the bind is not loopback, because a wildcard
  bind has no address anything can connect to and publishing `http://0.0.0.0:8765/v1` as
  the URL to configure is simply wrong. This is also the URL the harness advertises in
  `capture.target_must_be_preconfigured_for` and probes.
- **`capture_token`** is required in public mode. The capture is on the open internet;
  without a token any traffic at all could enter the capture record. Configure the target's
  **upstream API key** to this value — every OpenAI-compatible gateway forwards it as
  `Authorization: Bearer …`, which is what the capture checks. An
  `x-conformance-capture-token` header is accepted too, for gateways that rewrite auth.

**TLS is assumed to be terminated by your tunnel.** The capture speaks plaintext HTTP/1.x
behind it. The harness implements no certificate handling and does not need to: both
hosted gateways checked below require an `https://` upstream, and every common tunnel
already provides one.

> Public mode is a **weaker observation** and the report says so in `capture.mode` and in
> `checks.configured_upstream_boundary.capture_mode`. Attribution rests on a token the
> target was configured with, rather than on the address being unreachable from outside.
> Do not compare a public run and a loopback run as equivalent evidence, and do not mix
> them in one column.

#### Traffic that is not the target's

A public capture **will** receive scan traffic. It is recorded and reported, never
silently dropped:

- `unattributed_requests` — requests that did not present the capture token. Expected to
  be non-zero on a public address. **Does not fail the check**: gating on it would fail a
  valid run because of unrelated traffic.
- `unattributed_uninspectable_requests` — of those, how many the harness could not parse.
  Ordinary noise on a public address (TLS handshakes into a plaintext port, random
  probes), so it does not fail the check either.
- `unattributed_leaked_entity_types` — protected fixture values found in unattributed
  traffic. This **does** fail the boundary check, because the fixture is synthetic and
  unrelated traffic should not contain it. It is reported in its own field rather than folded into
  `leaked_entity_types`, so the artifact never asserts the target sent something a
  stranger sent. The unattributed haystacks are also inspected separately and never joined
  into the target's channels — concatenating anonymous internet traffic into the
  cross-request joins would let a third party who knows the fixture and your capture URL
  manufacture a leak finding against the measured gateway.

#### From the CLI

Public mode is fully supported from the command line:

```bash
export CONFORMANCE_CAPTURE_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
pii-leak-benchmark \
  --target-base-url https://the-gateway-under-test.example/v1 \
  --iterations 10 \
  --capture-port 8765 \
  --capture-public-url https://your-tunnel.example/v1 \
  --json-out HTTP_CONFORMANCE.json
```

Prefer the `CONFORMANCE_CAPTURE_TOKEN` environment variable over `--capture-token`:
process listings show argv, so a token passed as a flag is readable by every other user
on the host. The environment variable takes precedence when both are set. The token is
never written to the report or to an error message.

`pii-leak-benchmark` is the harness command, and it is the only one. `llm-shield-proxy
benchmark` runs this project's local in-process profile and nothing else; pass it
`--target-base-url` and it points you here because external targets use the standalone
benchmark command.

### Can a hosted gateway actually be pointed at your capture?

Checked against vendor documentation rather than assumed. Both can, and both require
HTTPS and a publicly routable host — which is exactly what the tunnel provides.

| Target | Custom upstream? | Mechanism | Constraint that matters |
|---|---|---|---|
| Cloudflare AI Gateway | Yes | **Custom Providers** (Beta): `POST /accounts/{id}/ai-gateway/custom-providers` with a `base_url`, or the dashboard's **Add Custom Provider**. Documented use case: "Connect to your organization's self-hosted AI models". | `base_url` **must start with `https://`**. Needs an API token with `AI Gateway - Edit`. |
| Portkey (hosted) | Yes | **`custom_host`**, as the `x-portkey-custom-host` request header, an SDK argument, a Gateway Config target, or Model Catalog's "Local/Privately hosted provider". `forward_headers` passes an `Authorization` header through unprocessed. | "Portkey blocks requests to private and reserved IP ranges by default" — a loopback or RFC1918 address will not work, a public tunnel hostname will. `custom_host` must include the `/v1` path. |

Because `x-portkey-custom-host` is a per-request header, a Portkey run needs no dashboard
configuration at all: pass it with `--target-header`/`extra_headers` and the harness points
the target at the capture itself.

This is a documentation finding, not a measured run. Neither vendor has been run against
the harness, and both rows in the comparison table stay `Not run` until a pinned
configuration and a raw artifact exist.

### The capture self-probe

Before any target traffic, the harness sends one request to its own capture URL and
aborts the run unless the capture recorded it. A run whose capture cannot be reached
measures nothing, and the report it would otherwise emit is schema-valid and reads as a
gateway failure.

This replaces an earlier attempt to make the socket bind refuse a stolen port, which did
not work: measured on Windows 11, the hijack happens when the two sockets bind *different*
addresses, and there it happens with or without `SO_REUSEADDR`, while the matching-address
case was already refused. Probing the channel catches every hijack shape on every
platform, and also catches a firewall, a stray `HTTP_PROXY` environment variable, and a
dead server.

The probe uses a per-run secret path with no digits in it, and its record is bucketed out
of `captured_requests`, the correlation count and the leak haystacks, so it cannot move
any number a verdict is computed from. The path is appended to the capture **base** URL
(`…/v1/__conformance_capture_probe__/…`) and matched as a suffix, so a reverse proxy that
forwards only `/v1/*`, or that rewrites the prefix, does not turn a valid setup into an
aborted run. The 24-letter secret is what makes it unguessable; the prefix carries no
security.

`capture.self_probe` records the result. In public mode it also probes
`capture_public_url` and records `advertised_url_reachable`. A public URL answered by a
*different* server aborts the run. A public URL that is merely unreachable **from the
harness** does not: `host.docker.internal` resolves inside a container and often not on
the host, so aborting there would reject a valid setup. The field is published instead,
which is what lets a reader tell a broken tunnel apart from a gateway that sent the
traffic elsewhere.

### What the boundary check inspects

Every request the target makes to the capture origin is recorded — any path, any method,
the request line, the request headers, chunk extensions, trailers, and the body — because a
gateway that posts raw values to a sibling route, or carries them in a metadata header,
has leaked them just as surely as one that puts them in the chat payload. Bodies are read
under both `content-length` and chunked framing, decompressed when `content-encoding` says
to, then walked over every JSON type: strings, numbers, dictionary keys, character-code
arrays, and base64, hex or percent-encoded runs found inside strings. Values are matched
literally, across adjacent fragments, and with separators stripped, and each channel is
also joined in order across captured requests. The `inspection_scope` field in every report
states the coverage that run actually applied.

A request the harness cannot fully inspect — unparseable, or too large or too deeply nested
to walk within budget — is counted in `uninspectable_requests` and **fails** the boundary
check. Not having looked is not the same as having found nothing.

`captured_requests: 0` also fails the check, and the harness cannot tell you why —
see [Before you publish a row](#before-you-publish-a-row--run-validity).

Each run embeds a random five-word marker in the prompt, and at least three of those words
must come back in a captured request for the boundary check to count it. Without that,
a target can exfiltrate to its real upstream and satisfy the check with one unrelated
request to the capture server. The words are mundane nouns rather than a high-entropy
token, because a conforming gateway's secret and person detectors redact the latter.

### Before you publish a row — run validity

These are the conditions that would make **this particular run** meaningless. They are the
ones to check against the report's fields before treating an artifact as a result, and the
report lists them under `limitations.run_validity`.

- **The target was never configured to use the capture.** The profile does not install or
  configure the target and cannot confirm the configuration took effect.
- **`captured_requests: 0`.** This fails the boundary check and the harness *cannot tell
  you why*. Never configured, cannot reach the capture, and sent the traffic elsewhere all
  produce the same report. Check the target's configured upstream, and
  `capture.self_probe.advertised_url_reachable`, before reading it as leak evidence. Do
  not publish a zero-capture run as a result for the target.
- **The capture was not reachable.** It speaks plaintext HTTP/1.x, so the target must be
  able to open a plaintext connection to the advertised address (behind your TLS-terminating
  tunnel, in public mode). A gateway that only egresses to an operator-fixed upstream it
  will not let you change cannot be measured at all.
- **Capture mode.** A public run is weaker than a loopback run. See `capture.mode`.
- **Unattributed traffic.** On a public capture, expected and not a defect on its own; see
  the fields above.
- **An orthogonal policy rejection.** Authentication, rate limits, blast-radius controls
  and circuit breakers must permit the evaluation traffic. A policy rejection is a profile
  non-pass, not evidence that protected data leaked. `status_codes` and
  `iterations_measured` record it.
- **Over-budget or unparseable captures.** Failed closed as uninspectable. That is a
  non-pass, not a leak finding.
- **HTTP/2-only upstreams.** The capture observes HTTP/1.x; anything else fails closed as
  uninspectable, which is a statement about the harness, not the target.
- **A reversibly masked marker.** `correlated_requests: 0` with non-zero
  `captured_requests` can mean the gateway masked the run marker.
  `marker_words_observed_max` distinguishes a partly redacted marker from one that never
  arrived.

### What this method can never see — permanent limits

These hold in every run, and no configuration changes them. The report lists them under
`limitations.method_limits`.

- **Finite observation window.** It ends when the client iterations finish. Egress
  deliberately deferred until after capture shutdown is outside the run.
- **Covert channels.** The profile inspects HTTP application data, not encodings in
  request counts, ordering, timing, connection metadata, packetization, or DNS/TLS
  metadata.
- **One destination.** Leak detection covers the configured capture origin only. Egress to
  any other destination is outside what this profile can observe.
- **Bounded reassembly.** Encoded and fragmented values are recovered within a request,
  and ordered fragments within the same channel across requests, but not arbitrary
  fragments separated by unrelated values or moved between channel types.
- **Not a detector-accuracy measurement.** The synthetic fixture does not establish
  population-level accuracy.
- **Not RSS, audit evidence, or public-model behaviour.** None of these are evaluated.
- **Latency has no threshold** and includes local HTTP and capture-server work.
- **`fragmentation_safety` cannot distinguish** per-token streaming from a fully buffered
  response emitted as a few chunks.
- **Identity is a label.** Implementation name and version are operator-supplied, not
  measured. Any `attestation` block is self-reported run metadata, not third-party
  verification.

### Reading a non-pass

Start with `outcome`, not `passed`. `passed` is the raw measurement -- did all five checks
pass -- and it is deliberately not the publishable verdict. `outcome` is derived from what
the product claims about PII redaction, what was configured, and then the measurement:
`fail` means protected data reached the capture origin and nothing else does.
`no-leak-profile-not-met` is a non-pass with no leak (typically a one-way anonymizer that
never restores the value); `not-applicable` means the product never offered redaction;
`redaction-not-enabled`, `inconclusive` and `claim-unstated` are not verdicts at all.
`outcome_rationale` spells out which applies. See the
[results table](./results#what-a-row-is-allowed-to-say).


The five checks measure different things and a non-pass in one is not a leak finding in
another. Read `configured_upstream_boundary` first: `leaked_entity_types` is the only field
that reports protected data reaching the capture origin. The rest describe behaviour.

- `response_fidelity` compares the client-visible stream to the value sent. A gateway that
  anonymizes one-way — hashing, masking, or replacing with a type label, with no rehydration —
  cannot reconstruct it and fails here while leaking nothing. `fragmentation_safety` gates on
  the same reconstruction, so it fails alongside it; `response_reconstructed` records which
  term was responsible.
- `sse_validity` and `client_observed_latency` fail when the target refuses traffic. An
  authentication, rate-limit, quota, or circuit-breaker rejection is a profile non-pass and
  not evidence about privacy. `status_codes` and `iterations_measured` record it.
- `correlated_requests: 0` with a non-zero `captured_requests` can mean the gateway masked the
  run marker. `marker_words_observed_max` reports how much of the five-word marker survived:
  a partly redacted marker leaves some words, a target that never received the prompt leaves
  none.

Publish the per-check results, not just the top-level `passed`.

Use `--target-base-url capture://self` to publish a raw OpenAI-compatible baseline. It is expected
to fail the configured-upstream privacy check; that negative control proves the results format can
represent a loss rather than only successful project runs.

This profile does not install or configure the target. Publish the target image/package digest,
gateway configuration with secrets removed, command, raw JSON report, and any deviation.

## Contribute an independent reproduction

Open a GitHub Discussion or pull request containing the unmodified JSON artifact, host/runtime
description, command, and your relationship to the measured implementation, if any. Independent
artifacts are listed separately from project-run results.
