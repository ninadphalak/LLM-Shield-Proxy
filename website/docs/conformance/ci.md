---
title: Catch PII leaks before they merge
sidebar_position: 1
---

# Catch PII leaks before they merge

Your gateway is meant to strip personal data before it reaches the model provider. This job
checks that it still does, on every pull request, and fails the build when it does not.
Setup is one file and three lines. No account, no API key, no paid model.

## 1. Add the workflow

Save this as `.github/workflows/pii-leak-check.yml`:

```yaml
name: PII leak check

on: [pull_request]

permissions:
  contents: read

jobs:
  pii-leak:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7

      # Build or install YOUR gateway here, however your project does it.
      - uses: actions/setup-python@v7
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt

      - uses: ninadphalak/LLM-Shield-Proxy@benchmark-v0.3.0
        with:
          target-base-url: http://127.0.0.1:4000/v1
          start-command: ./scripts/start-test-gateway.sh
          upstream-env: UPSTREAM_BASE_URL
```

## 2. Change three lines

| Line | Put your own |
| :--- | :--- |
| `target-base-url` | The `/v1` address your gateway listens on |
| `start-command` | A command that starts it in the foreground and keeps running |
| `upstream-env` | The environment variable it reads for its provider URL, such as `OPENAI_BASE_URL` |

## 3. Open a pull request

It runs on every pull request from now on, takes about a minute, and shows up as a green or
red check in the PR's checks list. Click **Details** on it to see what it found:

| Data type | Baseline | Current | Next step |
| :--- | :--- | :--- | :--- |
| EMAIL | contained | leak | Enable or repair request redaction for this format. |
| SSN | contained | contained | No leak observed for this test shape. |

Red means your gateway sent that value to the provider unmasked, and the job blocks the merge
if you require the check. Nobody is emailed and nothing is sent anywhere: the result lives in
the pull request.

That is the whole setup. Everything below is optional.

---

## Optional: the rest

<details>
<summary><b>Show the result on your README</b></summary>

GitHub publishes a status badge for the workflow. Add it to your README, replacing
`OWNER/REPO`:

```markdown
[![PII leak check](https://github.com/OWNER/REPO/actions/workflows/pii-leak-check.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/pii-leak-check.yml)
```

It tracks the default branch and turns red the moment a leak lands there. On a public
repository anyone can see it, which is the point: it is a claim a reader can click and check
for themselves rather than take your word for.

</details>

<details>
<summary><b>The four possible results</b></summary>

| Result | Meaning | Job status |
| :--- | :--- | :--- |
| CLEAN | Required checks completed for the selected duty | Pass |
| LEAK | Synthetic values were observed upstream | Fail |
| CHECK FAILED | Response or transport checks failed without an observed leak | Fail |
| NOT MEASURED | Setup, attribution, inspection or comparison was incomplete | Fail |

Existing leaks also fail the current job. A no-regression result does not excuse them.

Locally, exit 0 means clean, 1 means a measured failure, and 2 means incomplete measurement.

</details>

<details>
<summary><b>Compare against your previous version</b></summary>

Add a second gateway and the summary separates new failures from ones you already had. The
runner builds both versions; the old one needs no permanent deployment.

```yaml
- uses: actions/checkout@v7
  with:
    path: candidate
- uses: actions/checkout@v7
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

The baseline is measured first, then the candidate, with identical fixture values, prompt
context, model, profile, seed and expectations. Record the code, policy and dependency
versions too: any of them can cause a difference.

A scheduled or push job needs an explicitly selected baseline commit or released image.

The cheaper option is to reuse the last result from your default branch instead of building
the old gateway at all. The job fetches it itself; nobody downloads anything:

```yaml
permissions:
  contents: read
  actions: read          # needed to read the earlier run

steps:
  - name: Fetch the last main-branch result
    env:
      GH_TOKEN: ${{ github.token }}
    run: |
      id=$(gh run list --workflow pii-leak-check.yml --branch main --status success              --limit 1 --json databaseId --jq '.[0].databaseId')
      gh run download "$id" --name pii-leak-benchmark --dir baseline || echo "no baseline yet"
  - uses: ninadphalak/LLM-Shield-Proxy@benchmark-v0.3.0
    with:
      target-base-url: http://127.0.0.1:4000/v1
      start-command: ./scripts/start-test-gateway.sh
      upstream-env: UPSTREAM_BASE_URL
      baseline-report: baseline/current.json
```

Two things to know before you rely on it. It compares across environments and time, so an
unrelated runner change can look like a gateway change; measuring both versions in one job
does not have that problem. And a baseline is rejected outright, as `NOT MEASURED`, if the
benchmark version, profile, model, iterations, seed or duty differ, or if either run was
incomplete. That is deliberate, but it means the job fails after you upgrade the benchmark
until your default branch has produced a fresh baseline.

</details>

<details>
<summary><b>One-way masking, and which data types are tested</b></summary>

`duty: restore` is the default and requires your gateway to give the caller back the original
text. Set `duty: anonymize` if your product masks in one direction on purpose; that excludes
the restoration checks but still requires upstream containment, valid SSE and completed
requests. The benchmark cannot infer which you intended, so say which.

`profile: pii-secrets-v1` is the default: email, a synthetic US SSN, a published test card,
and fixed AWS, GitHub and Slack credential examples. `profile: pii-v1` tests only the three
personal-data formats.

The default seed is `gateway-ci-v1`. Keep it stable for pull request comparisons and vary it
in a separate scheduled job.

</details>

<details>
<summary><b>Gateways in Docker, or on another host</b></summary>

On Linux runners a foreground `docker run --rm --network host ...` can reach the capture.
Pass the upstream setting from `BENCHMARK_UPSTREAM_BASE_URL` into the container, and build
your image first. A bridge-network container has its own loopback, so its `127.0.0.1` is not
the runner's.

For a gateway on another host, use the CLI's authenticated public-capture options from the
[hosted gateway guide](./hosted-gateway-runbook). The Action example above uses the runner's
local capture and cannot inspect arbitrary production traffic from a URL alone.

</details>

<details>
<summary><b>Run it on your laptop first</b></summary>

```bash
pip install "pii-leak-benchmark>=0.3.0"
pii-leak-benchmark ci --target-base-url http://127.0.0.1:4000/v1 \
  --start-command ./scripts/start-test-gateway.sh --upstream-env UPSTREAM_BASE_URL \
  --out privacy-check
```

`pii-leak-benchmark ci --help` lists every input, with the same meanings as the Action. Use a
fresh `--out` directory each time.

To pin the Git source instead of the PyPI package:

```bash
pip install "pii-leak-benchmark @ git+https://github.com/ninadphalak/LLM-Shield-Proxy@benchmark-v0.3.0#subdirectory=pii-leak-benchmark"
```

</details>

<details>
<summary><b>Files the job leaves behind</b></summary>

You do not need any of these to read the result. The table above is written straight into the
workflow run page, so you read it in the browser and there is nothing to fetch.

The job also uploads the underlying reports as an artifact called `pii-leak-benchmark`, on
failures too, and they are worth opening in two cases: feeding `current.json` back as a
baseline, and looking at exactly what arrived upstream when a result surprises you.

| File | Purpose |
| :--- | :--- |
| `summary.md` | Findings and suggested next steps |
| `current.json` | Operator result and comparison contract |
| `current.raw.json` | Underlying HTTP measurements |
| `baseline.json` | Optional previous operator result |
| `baseline.raw.json` | Raw baseline when measured during this job |
| `control.raw.json` | Negative-control evidence |

</details>

<details>
<summary><b>How it works, and what it does not cover</b></summary>

The Action stands up a synthetic model provider, points your gateway at it through
`upstream-env`, and records every request that arrives. It runs a negative control first, so
a run that measures nothing fails instead of passing quietly. It starts your gateway, waits
for its port, measures, writes the summary, and stops what it started. Startup logs are
suppressed, so run your command locally if startup fails.

The provider is listening before your start command runs, so a gateway that contacts its
provider during startup finds it there. Requests sent during startup are part of the measured
record, like any other request to the configured upstream.

Configure the same redaction policy you intend to ship. The benchmark measures your gateway;
it does not enable protection for you. Pin the Action to an immutable commit SHA if your
organization requires it. For a gateway already running and already pointed at the capture,
omit `start-command` and `upstream-env`. Authenticate to your gateway with
`CONFORMANCE_TARGET_API_KEY` and `CONFORMANCE_TARGET_HEADERS`.

**Limits.** The fixture uses a fixed set of formats. The values change every run, the formats
do not, so a program written for those formats can pass without being a general detector.
Credential examples are fixed shapes, not a representative sample. Only the capture and client
boundaries are observed: logs, internal stores and other outbound destinations are outside
this check. Run [response fragmentation tests](./reproduce-fragmentation) separately for
injected response data.

</details>
