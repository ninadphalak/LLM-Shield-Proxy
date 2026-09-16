---
title: Catch gateway regressions in CI
sidebar_position: 3
---

# Catch gateway regressions in CI

Test your gateway in the workflow that ships it. Each run gives you a table of tested data
types, failing behaviors and next steps. An optional previous version makes new failures
and improvements visible in the GitHub job summary.

## Set up once

The benchmark acts as a synthetic model provider at `http://127.0.0.1:8765/v1`. Your test
gateway must send upstream requests there; no paid model is needed. Configure the same
redaction policy you intend to ship. The benchmark does not enable protection for you.

After your existing dependency-install or image-build steps, add:

```yaml
- uses: ninadphalak/LLM-Shield-Proxy@benchmark-v0.3.0
  with:
    target-base-url: http://127.0.0.1:4000/v1
    start-command: ./scripts/start-test-gateway.sh
    upstream-env: UPSTREAM_BASE_URL
    target-model: conformance-model
    duty: restore
```

Set `start-command` to your foreground startup command and `upstream-env` to the variable
your gateway reads for its provider URL. A startup script can instead read
`BENCHMARK_UPSTREAM_BASE_URL` to generate a temporary YAML routing configuration.
These inputs are gateway-specific; the remaining steps are automatic. Pin the Action to
an immutable commit SHA when your organization requires it.

The synthetic provider is listening before your startup command runs, so a gateway that
contacts its provider while starting up, to list models or to check its credentials, finds
it there. Requests your gateway sends during startup are part of the measured record, like
any other request it sends to the configured upstream. The provider answers `GET /v1/models`
with a one-entry list, and any other startup path with a 404 it records.

The Action runs a negative control, waits for the gateway's port, measures it, writes the
summary, uploads reports, and stops processes it started. It needs no account or write token.
Startup logs are suppressed; run your command locally if startup fails. For a gateway already
running with capture routing configured, omit `start-command` and `upstream-env`.
Supply authentication through `CONFORMANCE_TARGET_API_KEY` and `CONFORMANCE_TARGET_HEADERS`.

## Understand the result

| Result | Meaning | Job status |
| :--- | :--- | :--- |
| CLEAN | Required checks completed for the selected duty | Pass |
| LEAK | Synthetic values were observed upstream | Fail |
| CHECK FAILED | Response or transport checks failed without an observed leak | Fail |
| NOT MEASURED | Setup, attribution, inspection or comparison was incomplete | Fail |

A candidate that stops masking email while keeping other protections produces:

| Data type | Baseline | Current | Next step |
| :--- | :--- | :--- | :--- |
| EMAIL | contained | leak | Enable or repair request redaction for this format. |
| SSN | contained | contained | No leak observed for this test shape. |

Existing leaks also fail the current job. A no-regression result does not excuse them.

## Select the duty and coverage

`duty: restore` requires restoration of the caller's original text after request masking.
`duty: anonymize` permits one-way transformation and excludes restoration-dependent checks.
It still requires upstream containment, valid SSE and completed requests. Declare this
expectation explicitly; the benchmark cannot infer your product's intended behavior.

`profile: pii-secrets-v1` tests email, a synthetic US SSN, a published test card and three
fixed AWS/GitHub/Slack credential examples. `profile: pii-v1` tests only the three personal-data
formats. Reports list the forms tested and their variation. Credential examples remain fixed
synthetic shapes, not a representative sample of all credentials.

The default seed is `gateway-ci-v1`. Keep it stable for PR comparisons; vary it in additional
scheduled jobs. A public fixed workload supports regression testing, not protection against a
gateway deliberately programmed to recognize the benchmark. Other privacy/security controls
and credential formats are outside these profiles.

## Compare the old gateway and the candidate

GitHub gives the job a temporary runner. It can build and run both versions there; the old
gateway does not need a permanent deployment. Check out the PR base and candidate into
separate directories, install/build each with isolated dependencies, then configure:

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
  with:
    path: candidate
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
  with:
    ref: ${{ github.event.pull_request.base.sha }}
    path: baseline
# Build both versions here in separate environments or container images.
- uses: ninadphalak/LLM-Shield-Proxy@benchmark-v0.3.0
  with:
    target-base-url: http://127.0.0.1:4000/v1
    start-command: cd candidate && ./scripts/start-test-gateway.sh --port 4000
    target-version: ${{ github.sha }}
    baseline-base-url: http://127.0.0.1:4001/v1
    baseline-start-command: cd baseline && ./scripts/start-test-gateway.sh --port 4001
    baseline-version: ${{ github.event.pull_request.base.sha }}
    upstream-env: UPSTREAM_BASE_URL
```

This example is for `pull_request`. A scheduled or push job needs an explicitly selected
baseline commit or released image. The baseline is measured first, followed by the candidate,
with identical fixture values, prompt context, model, profile, seed and expectations.
Record the code, policy and dependency versions too: any of them can cause a difference.

Alternatively, download `current.json` from a trusted prior main-branch run and supply its
path as `baseline-report`. That cheaper comparison uses historical observations and cannot
control changes to the environment. Different benchmark code, profile, model, iterations,
seed, duty or incomplete measurements are rejected. Upgrade the benchmark by remeasuring
the baseline. Your workflow chooses which prior artifact to trust; the Action never guesses.

## Docker and remote targets

On Linux runners, a foreground `docker run --rm --network host ...` can reach the loopback
capture. Pass the upstream setting from `BENCHMARK_UPSTREAM_BASE_URL` into the container.
Build your image first. A bridge-network container has its own loopback, so its `127.0.0.1`
does not refer to the runner.

For another host, use the CLI's authenticated public-capture options from the
[hosted gateway guide](./hosted-gateway-runbook). The managed-start Action example uses the
runner's local capture. It cannot inspect arbitrary production traffic just from a URL.

## Local runs and artifacts

Install the Git source release without depending on PyPI upload timing:

```bash
pip install "pii-leak-benchmark @ git+https://github.com/ninadphalak/LLM-Shield-Proxy@benchmark-v0.3.0#subdirectory=pii-leak-benchmark"
pii-leak-benchmark ci --target-base-url http://127.0.0.1:4000/v1 \
  --start-command ./scripts/start-test-gateway.sh --upstream-env UPSTREAM_BASE_URL \
  --out privacy-check
```

Use a fresh output directory each time. Exit 0 means clean, 1 means a measured failure, and
2 means incomplete measurement. Artifacts are retained on failure too:

| File | Purpose |
| :--- | :--- |
| `summary.md` | Findings and suggested next steps |
| `current.json` | Operator result and comparison contract |
| `current.raw.json` | Underlying HTTP measurements |
| `baseline.json` | Optional previous operator result |
| `baseline.raw.json` | Raw baseline when measured during this job |
| `control.raw.json` | Negative-control evidence |

Only the capture and client boundaries are observed. Logs, internal stores and other outbound
destinations are outside this check. Run [response fragmentation tests](./reproduce-fragmentation)
separately for injected response data. Historical manuscript reports and schemas are unchanged.
