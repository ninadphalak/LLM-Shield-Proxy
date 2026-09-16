---
title: Catch gateway regressions in CI
sidebar_position: 3
---

# Add a PII leak check to your CI

Your gateway is meant to strip personal data before it reaches the model provider. This job
proves it still does, on every pull request. Setup is three lines and no account.

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

The job sends synthetic personal data through your gateway and watches what reaches the
provider. It fails if anything arrives unmasked, and the job summary names the data type and
the next step:

| Data type | Baseline | Current | Next step |
| :--- | :--- | :--- | :--- |
| EMAIL | contained | leak | Enable or repair request redaction for this format. |
| SSN | contained | contained | No leak observed for this test shape. |

That is the whole setup. Everything below is optional.

---

## Optional: the rest

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
Alternatively, download `current.json` from a trusted prior main-branch run and pass its path
as `baseline-report`. That is cheaper and weaker: it compares across environments and time.
Different benchmark code, profile, model, iterations, seed, duty or incomplete measurements
are rejected. Remeasure the baseline after upgrading the benchmark.

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
fresh `--out` directory each time. To pin the source rather than the package, install
`git+https://github.com/ninadphalak/LLM-Shield-Proxy@benchmark-v0.3.0#subdirectory=pii-leak-benchmark`.

</details>

<details>
<summary><b>Files the job leaves behind</b></summary>

Uploaded as an artifact, on failure too:

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
