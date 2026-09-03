# Reproduce the Conformance Report

This guide explains how to run the conformance harness to generate a report, verify the artifact, and test gateways over HTTP.

## From a Source Checkout

To generate the conformance report locally, run the following two commands:

### 1. Install the packages

`ash
python -m pip install -e ./pii-leak-benchmark -e ".[dev]"
`

This command installs:
- pii-leak-benchmark: The endpoint-neutral HTTP profile package.
- llm-shield-proxy (with dev dependencies): The local gateway and test tools.

### 2. Run the local benchmark

`ash
llm-shield-proxy benchmark \
  --iterations 10000 \
  --json-out CONFORMANCE_LATEST.json
`

This command executes the proxy's **local, in-process conformance profile and microbenchmarks**.
It runs offline, without calling any external LLMs, and does not write any test PII to the final JSON report.

To ensure a specific revision is recorded (e.g., outside of GitHub Actions), set the environment variable:

**Bash:**
`ash
LLM_SHIELD_SOURCE_REVISION=d0f4834b8d05444bfc04d3d32cfcb3a148aaa51f \
  llm-shield-proxy benchmark --iterations 10000 --json-out CONFORMANCE_LATEST.json
`

**PowerShell:**
`powershell
 = git rev-parse HEAD
py -m llm_shield_proxy.cli benchmark --iterations 10000 --json-out CONFORMANCE_LATEST.json
`

## Verify the Artifact

After running the benchmark, check the output file (CONFORMANCE_LATEST.json) to confirm that:

1. schema ends in /v1.0.0.
2. source_revision matches the tested revision.
3. All six checks are present and display as passed.
4. Protected vector values (test PII) are absent from the report.
5. The timing scope explicitly excludes network and framework overhead (it only measures in-process components).
6. The memory scope distinguishes Python allocations from total process RSS.

*Note: Latency measurements are provided under microbenchmarks rather than as a pass/fail check.*

For production-shaped comparisons, refer to enchmarks/REPORTING.md. Please publish all runs, including unsuccessful ones and any deviations.

## Run the OpenAI-Compatible HTTP Profile

To test a target gateway over HTTP (rather than in-process), use the pii-leak-benchmark package.

If you are testing a hosted gateway (e.g., behind a vendor account), please consult the [hosted-gateway runbook](./hosted-gateway-runbook).

The HTTP profile works by acting as the target gateway's **upstream capture server**. There are two capture modes: **loopback** and **public**.

### Capture Mode: Loopback (Default)

In loopback mode, the capture server binds to 127.0.0.1. This requires the gateway to be running on the same host (e.g., local process, container, or pod). This mode guarantees that all received traffic belongs to the target.

`ash
CONFORMANCE_TARGET_API_KEY=local-evaluation-key \
pii-leak-benchmark \
  --target-base-url http://127.0.0.1:8000/v1 \
  --target-name implementation-under-test \
  --target-version pinned-version \
  --iterations 10 \
  --json-out HTTP_CONFORMANCE.json
`

If the target gateway runs in a container and needs a reachable host address, you can configure the harness to listen on a specific interface:

`python
capture_host="0.0.0.0",
capture_public_url="http://host.docker.internal:8765/v1",
capture_token="a-long-random-value",
`

*Ensure your host firewall restricts access to this port.*

### Capture Mode: Public

Hosted gateways cannot connect to your local loopback address. You must deploy the capture server on a reachable public address (e.g., a VPS or a tunnel like 
grok or cloudflared).

`python
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
`

**Requirements for Public Mode:**
- **capture_host**: Must bind to a reachable interface.
- **capture_public_url**: Must be the public base URL configured on the target gateway.
- **capture_token**: Used to identify traffic from the target. The target gateway should send this as its upstream Authorization bearer token, or as an x-conformance-capture-token header.

*Note: Your tunnel must terminate TLS, as the capture server only accepts plaintext HTTP/1.x.*

#### Handling Unattributed Traffic

Public capture servers may receive random internet scanning traffic. The benchmark handles this safely:
- unattributed_requests: Requests lacking the capture token.
- unattributed_uninspectable_requests: Unparseable requests.
- unattributed_leaked_entity_types: Protected test values found in unattributed traffic.

These metrics do not fail the boundary check unless the target gateway's own traffic contains leaked test values.

#### CLI Usage for Public Mode

You can run public mode from the command line:

`ash
export CONFORMANCE_CAPTURE_TOKEN="rl55W7ikx2nF7sqC7Bkjb-PKlNc9Jm_C5VbFJ8Y3Knw"
pii-leak-benchmark \
  --target-base-url https://the-gateway-under-test.example/v1 \
  --iterations 10 \
  --capture-port 8765 \
  --capture-public-url https://your-tunnel.example/v1 \
  --json-out HTTP_CONFORMANCE.json
`

It is recommended to use the CONFORMANCE_CAPTURE_TOKEN environment variable rather than the --capture-token flag to prevent the token from appearing in system process lists.

### Configuring Hosted Gateways

Hosted gateways (like Cloudflare AI Gateway or Portkey) require a publicly routable upstream URL (starting with https://).

- **Cloudflare AI Gateway**: Configure a "Custom Provider" pointing to your capture URL.
- **Portkey**: Use the x-portkey-custom-host header to point to your capture URL.

### The Capture Self-Probe

Before testing the target, the harness sends a probe request to its own capture URL to verify connectivity. If the capture server does not record this probe, the run aborts. This prevents tests from failing due to misconfigured firewalls or port conflicts.

### What the Boundary Check Inspects

The capture server records and inspects every request from the target. It supports content-length, chunked framing, declared compression, JSON, and encoded text (base64, hex, percent-encoding).

The check searches for literal values, adjacent fragments, and values with separators removed. If a request is too large or malformed to parse, it counts as uninspectable_requests and fails the boundary check.

The test prompt includes a random five-word marker. At least three words must appear in a captured request for it to count, ensuring the gateway actually forwarded the prompt to the capture server.

### Run Validity

The following conditions will invalidate a test run and are listed under limitations.run_validity:
- The target was never configured to use the capture server.
- captured_requests: 0 (this fails the boundary check).
- The capture was unreachable from the target.
- Policy rejections (e.g., authentication failure, rate limits).
- Unparseable captures.
- The target used HTTP/2 instead of HTTP/1.x.

### Permanent Method Limits

The following limitations apply to all runs (limitations.method_limits):
- Observation ends when client iterations finish.
- Covert channels (e.g., timing, packetization) are not inspected.
- Only requests sent to the capture server are monitored.
- It does not measure population-level detector accuracy.
- Process RSS, audit logging, and public-model behavior are not evaluated.
- Latency measurements include local HTTP overhead.

### Interpreting a Non-Pass

Check the outcome field to understand the result:
- ail: The gateway leaked unmasked test values.
- 
o-leak-profile-not-met: No leak occurred, but another requirement failed (e.g., one-way anonymization without rehydration).
- 
ot-applicable: The product does not support redaction.
- 
edaction-not-enabled / inconclusive: The run could not determine a verdict.

Review outcome_rationale for details. A failure in one check does not automatically mean data leaked. Always check configured_upstream_boundary.leaked_entity_types to see if unmasked values were found.

## Contribute an Independent Reproduction

To contribute an independent reproduction, open a GitHub Discussion or pull request containing your unmodified JSON artifact, host environment details, command used, and your relationship to the measured implementation.
