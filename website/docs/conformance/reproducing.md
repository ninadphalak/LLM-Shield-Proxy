---
sidebar_position: 5
title: Reproduce the conformance report
---

# Reproduce the conformance report

How to run the v1.0.0 conformance harness: the local in-process profile, and the HTTP
profile against a gateway.

Looking for the paper's fragmentation result instead? That is a different, smaller
experiment: [reproduce the fragmentation result](./reproduce-fragmentation).

## Steps: local profile

Runs offline. No gateway, no API key, no model.

### 1. Install

```bash
python -m pip install -e ./pii-leak-benchmark -e ".[dev]"
```

### 2. Run

```bash
llm-shield-proxy benchmark \
  --iterations 10000 \
  --json-out CONFORMANCE_LATEST.json
```

To record a specific revision in the report, set `LLM_SHIELD_SOURCE_REVISION` first.
GitHub Actions supplies `GITHUB_SHA` instead and needs no flag.

**Bash:**

```bash
LLM_SHIELD_SOURCE_REVISION=$(git rev-parse HEAD) \
  llm-shield-proxy benchmark --iterations 10000 --json-out CONFORMANCE_LATEST.json
```

**PowerShell:**

```powershell
$env:LLM_SHIELD_SOURCE_REVISION = git rev-parse HEAD
llm-shield-proxy benchmark --iterations 10000 --json-out CONFORMANCE_LATEST.json
```

### 3. Check the report

Open `CONFORMANCE_LATEST.json` and confirm:

1. `schema` ends in `/v1.0.0`.
2. `source_revision` matches the revision you tested.
3. All six checks are present and passed.
4. No test PII values appear anywhere in the file.
5. The timing scope says it excludes network and framework overhead.
6. The memory scope distinguishes Python allocations from process RSS.

## Steps: HTTP profile, local gateway

Tests any OpenAI-compatible gateway running on the same host. The harness starts a
capture server on loopback and acts as the gateway's model provider.

### 1. Install

```bash
pip install pii-leak-benchmark
```

### 2. Point the gateway at the capture server

Configure the gateway under test to use `http://127.0.0.1:8765/v1` as its upstream model
provider.

### 3. Run

```bash
CONFORMANCE_TARGET_API_KEY=local-evaluation-key \
pii-leak-benchmark \
  --target-base-url http://127.0.0.1:8000/v1 \
  --target-name implementation-under-test \
  --target-version pinned-version \
  --iterations 10 \
  --json-out HTTP_CONFORMANCE.json
```

If the gateway runs in a container, bind the capture server to a reachable interface
instead:

```bash
pii-leak-benchmark \
  --target-base-url http://127.0.0.1:8000/v1 \
  --capture-host 0.0.0.0 \
  --capture-public-url http://host.docker.internal:8765/v1 \
  --json-out HTTP_CONFORMANCE.json
```

Restrict that port at the host firewall.

### 4. Read `outcome`

| Value | Meaning |
| :--- | :--- |
| `pass` | No unmasked test value reached the capture server, and every profile requirement was met. |
| `fail` | The gateway sent an unmasked test value to the capture server. |
| `no-leak-profile-not-met` | Nothing leaked, but another requirement failed - for example one-way anonymization with no restoration. |
| `not-applicable` | The product does not offer redaction. |
| `redaction-not-enabled` | Redaction exists but was not turned on for this run. |
| `inconclusive` | The run could not be attributed to the target. |
| `claim-unstated` | No redaction claim was recorded. The report is not publishable as a results row. |

`outcome` is derived from the recorded claim plus the measurement, not typed by the
submitter. A hand-edited report fails schema validation.

Read `outcome_rationale` for the reason, and
`configured_upstream_boundary.leaked_entity_types` to see whether unmasked values were
actually found. A failed check is not by itself a leak.

## Steps: HTTP profile, hosted gateway

A hosted gateway cannot reach your loopback address, so the capture server needs a public
address - a VPS, or a tunnel such as `ngrok` or `cloudflared`. See the
[hosted-gateway runbook](./hosted-gateway-runbook) first.

### 1. Expose the capture server

Your tunnel must terminate TLS. The capture server itself accepts plaintext HTTP/1.x only.

### 2. Configure the gateway's upstream

Point the gateway at your public capture URL. It must send the capture token as its
upstream `Authorization` bearer token, or as an `x-conformance-capture-token` header.

- **Cloudflare AI Gateway**: configure a Custom Provider pointing at the capture URL.
- **Portkey**: use the `x-portkey-custom-host` header.

### 3. Run

```bash
export CONFORMANCE_CAPTURE_TOKEN="rl55W7ikx2nF7sqC7Bkjb-PKlNc9Jm_C5VbFJ8Y3Knw"
pii-leak-benchmark \
  --target-base-url https://the-gateway-under-test.example/v1 \
  --iterations 10 \
  --capture-host 0.0.0.0 \
  --capture-port 8765 \
  --capture-public-url https://your-tunnel.example/v1 \
  --json-out HTTP_CONFORMANCE.json
```

Use `CONFORMANCE_CAPTURE_TOKEN` rather than `--capture-token`, so the token does not
appear in process lists.

The Python API takes the same arguments:

```python
from pii_leak_benchmark import run_http_conformance

report = run_http_conformance(
    "https://the-gateway-under-test.example/v1",
    api_key="...",
    iterations=10,
    capture_host="0.0.0.0",
    capture_port=8765,
    capture_token="a-long-random-value",
    capture_public_url="https://your-tunnel.example/v1",
)
```

## Steps: contribute a reproduction

Open a GitHub Discussion or pull request with:

1. The unmodified JSON report.
2. Host environment details.
3. The exact command you ran.
4. Your relationship to the implementation you measured.

Publish unsuccessful runs and deviations too. See [submitting a result](./submitting).

## Explanation

Everything below is context. None of it is needed to run the steps.

### What the local profile measures

`llm-shield-proxy benchmark` runs the proxy's in-process conformance checks and
microbenchmarks. It never calls an external model and writes no test PII into the report.

Latency appears under `microbenchmarks` as a measured distribution, not as a pass/fail
check, because the numbers describe in-process components only. They are not end-to-end
proxy latency. For production-shaped comparisons see `benchmarks/REPORTING.md`.

### Why the HTTP profile needs a capture server

The question the profile answers is what the gateway sent to its model provider. The only
way to see that is to be the model provider. The harness therefore stands up a capture
server, the gateway is configured to treat it as upstream, and the harness inspects every
request it receives. It then streams a response back one character per SSE event, so
value restoration is tested against the hardest fragmentation the transport allows.

This measures network input and output only. Process RSS and audit integrity need the
local profile.

### Loopback versus public capture

Loopback binds to `127.0.0.1`, which guarantees that every request the capture server sees
came from the target. Public mode gives up that guarantee in exchange for reaching a
hosted gateway, so the report separates traffic that carried the capture token from
traffic that did not:

- `unattributed_requests` - requests without the token.
- `unattributed_uninspectable_requests` - requests that could not be parsed.
- `unattributed_leaked_entity_types` - test values found in unattributed traffic.

None of these fail the boundary check on their own. Only the target's own traffic can.

### The capture self-probe

Before testing anything, the harness sends a probe to its own capture URL. If the capture
server does not record that probe, the run aborts rather than reporting a clean result
produced by a firewall rule or a port conflict.

### What the boundary check inspects

Every request from the target: URL, headers, HTTP framing and body. It handles
content-length and chunked framing, declared compression, JSON, and encoded text
(base64, hex, percent-encoding). It searches for literal values, adjacent fragments, and
values with separators removed.

A request too large or malformed to parse counts as `uninspectable_requests` and fails the
boundary check. It is never assumed clean.

The test prompt carries a random five-word marker. At least three of those words must
appear in a captured request for it to count, which is how the harness knows the gateway
actually forwarded the prompt rather than dropping it.

### What invalidates a run

Listed in `limitations.run_validity`:

- The target was never configured to use the capture server.
- `captured_requests: 0`.
- The capture server was unreachable from the target.
- Policy rejections - authentication failure, rate limits.
- Unparseable captures.
- The target used HTTP/2 rather than HTTP/1.x.

### Permanent limits of the method

Listed in `limitations.method_limits`, and true of every run:

- Observation ends when client iterations finish.
- Covert channels - timing, packetization - are not inspected.
- Only requests sent to the capture server are observed.
- It does not measure population-level detector accuracy.
- Process RSS, audit logging and public-model behaviour are out of scope.
- Latency figures include local HTTP overhead.

## Related

- [Reproduce the fragmentation result](./reproduce-fragmentation)
- [Hosted-gateway runbook](./hosted-gateway-runbook)
- [Published results](./results)
- [Submit a run](./submitting)
