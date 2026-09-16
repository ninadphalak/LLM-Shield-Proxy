---
title: Catch gateway regressions in CI
sidebar_position: 3
---

# Catch gateway regressions in CI

Test your gateway in the workflow that ships it. Each run gives you a table of tested data
types, failing behaviors and next steps. An optional previous version makes new failures
and improvements visible in the GitHub job summary.

The check runs entirely on the GitHub runner. It needs no account, no API key for this
project, no write token and no paid model.

## What you need before you start

1. **An OpenAI-compatible gateway in a GitHub repository.** Anything that accepts
   `POST /v1/chat/completions` with `"stream": true` and forwards to a model provider.
2. **A command that starts it in the foreground** and keeps running, such as a script in
   your repository. The Action runs this command and stops the process afterwards.
3. **A way to tell it where its provider is**, normally an environment variable such as
   `OPENAI_BASE_URL` or `UPSTREAM_BASE_URL`. This is the one setting you have to name,
   because no two gateways spell it the same way.

That is the whole list. If your gateway is already running somewhere the runner can reach,
and already points at the capture, you can skip items 2 and 3.

## The complete workflow

Copy this into your repository as `.github/workflows/pii-leak-check.yml`, then change the
three marked lines to match your gateway:

```yaml
name: PII leak check

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  pii-leak:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7

      # Build or install YOUR gateway here, however your project does it.
      # This example is a Python project; substitute your own steps.
      - uses: actions/setup-python@v7
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt

      - uses: ninadphalak/LLM-Shield-Proxy@benchmark-v0.3.0
        with:
          # 1. Where your gateway will listen.
          target-base-url: http://127.0.0.1:4000/v1
          # 2. How to start it, in the foreground.
          start-command: ./scripts/start-test-gateway.sh
          # 3. The variable it reads for its provider URL.
          upstream-env: UPSTREAM_BASE_URL
          duty: restore
```

The job fails when a synthetic value reaches the upstream, and the summary names which data
type and what to do about it. Nothing else is required to get a first result.

### What the three lines mean

`target-base-url` is your gateway's own `/v1` address on the runner. `start-command` is run
in the foreground and stopped after measurement; startup logs are suppressed, so run the
command locally if startup fails. `upstream-env` receives the address of a synthetic model
provider the Action stands up for the run, in place of the real one. A startup script can
instead read `BENCHMARK_UPSTREAM_BASE_URL`, which is always set, and write a temporary
routing configuration from it.

Configure the same redaction policy you intend to ship. The benchmark measures your gateway.
It does not enable protection for you. Pin the Action to an immutable commit SHA when your
organization requires it.

The synthetic provider is listening before your startup command runs, so a gateway that
contacts its provider while starting up, to list models or to check its credentials, finds
it there. Requests your gateway sends during startup are part of the measured record, like
any other request it sends to the configured upstream. The provider answers `GET /v1/models`
with a one-entry list, and any other startup path with a 404 it records.

The Action runs a negative control, waits for the gateway's port, measures it, writes the
summary, uploads reports, and stops processes it started. For a gateway already running with
capture routing configured, omit `start-command` and `upstream-env`. Supply authentication to
your gateway through `CONFORMANCE_TARGET_API_KEY` and `CONFORMANCE_TARGET_HEADERS`.

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

Run the same check on your own machine before you commit a workflow:

```bash
pip install "pii-leak-benchmark>=0.3.0"
pii-leak-benchmark ci --target-base-url http://127.0.0.1:4000/v1 \
  --start-command ./scripts/start-test-gateway.sh --upstream-env UPSTREAM_BASE_URL \
  --out privacy-check
```

`pii-leak-benchmark ci --help` lists every input with the same meanings as the Action. To pin
the source instead of the package, install
`git+https://github.com/ninadphalak/LLM-Shield-Proxy@benchmark-v0.3.0#subdirectory=pii-leak-benchmark`.

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
