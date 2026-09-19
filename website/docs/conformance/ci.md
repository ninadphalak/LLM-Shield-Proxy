---
title: Catch PII leaks before they merge
sidebar_position: 1
---

# Catch PII leaks before they merge

Your gateway is meant to strip personal data before it reaches the model provider. This job
checks that it still does, on every pull request, and fails the build when it does not.
Setup is one file and three lines. No account, no API key, no paid model.

**What you get for it:** the build turns red on the pull request that introduces a leak, naming
the data type, showing you the value as it left your network, and saying what to do about it. A
redaction rule that quietly stops matching is otherwise invisible until someone finds customer
data in a provider log.

## See what it looks like first, with no gateway at all

You do not need to set anything up to see the output. This runs the same check against a direct
connection with no gateway in the middle, which means every value leaks, on purpose:

```bash
pipx run pii-leak-benchmark selfcheck --target-base-url capture://self
```

It starts a local capture, sends six kinds of synthetic data through, and reports what arrived:

```
  LEAK

  Raw fixture values reached the upstream: AWS_ACCESS_KEY_ID, CREDIT_CARD,
  EMAIL, GITHUB_TOKEN, SLACK_TOKEN, SSN.

LEAK  EMAIL reached the model provider
      you sent:         xjtfstbe@example.com
      the provider saw: xjtfstbe@example.com  <- nothing removed it on the way
```

That is the failure the job catches. Nothing leaves your machine, the values are synthetic, and
`capture://self` is the negative control: if it ever reports anything but `LEAK`, the instrument
itself is broken. Point `--target-base-url` at a real gateway instead and you have the same check
your CI will run.

### No gateway of your own? Test someone else's

`capture://self` has no gateway in it, so it measures nothing about any product. To get a real
result you need something in the middle, and standing one up takes about two minutes. Any
OpenAI-compatible gateway works; this is one, installed from PyPI:

```bash
pip install llm-shield-proxy "uvicorn[standard]"

UPSTREAM_BASE_URL=http://127.0.0.1:8765 VALID_VIRTUAL_KEYS=sk-demo   python -m uvicorn llm_shield_proxy.api.main:app --port 4000 &

pii-leak-benchmark selfcheck --target-base-url http://127.0.0.1:4000/v1 --target-api-key sk-demo
```

`8765` is the port the check listens on, so the gateway is told to send its upstream traffic
there and the check sees exactly what left. Swap in LiteLLM, LLM Guard, Portkey or your own
build the same way: point the gateway's upstream at the capture, point the check at the gateway.

The [results wall](./who-has-run-it) lists what other gateways scored and how each was
configured, which is the quickest way to find one worth trying.

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

      - uses: ninadphalak/LLM-Shield-Proxy@benchmark-v0.3.1
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

It runs on every pull request from now on and takes about a minute. It appears as a green or red
check in the PR's checks list, and the full result is written to the **job summary**, which is
the page you land on from **Details**. You do not have to read the logs.

The summary opens with the verdict, one of `CLEAN`, `LEAK`, `CHECK FAILED` or `NOT MEASURED`,
then a row per data type:

| Data type | Baseline | Current | Next step |
| :--- | :--- | :--- | :--- |
| EMAIL | not run | leak | Enable or repair request redaction for this format. |
| SSN | not run | contained | No leak observed for this test shape. |

`Baseline` reads `not run` until you configure a previous version to compare against, which is
optional and covered below.

Under that, for anything that leaked, a section called **What leaked, and why it matters** shows
the value as you sent it beside the value the provider received, and says why that particular
data type is worth caring about. Values there are printed as shapes such as `<EMAIL>`: the
summary is visible to everyone who can see the pull request, so the specimens go to the terminal
of the machine that ran the check instead.

Then the required behaviour checks, pass or fail, and a **What to do next** line written for the
verdict you actually got rather than a generic one.

Red means your gateway sent that value to the provider unmasked, and the job blocks the merge
if you require the check. Nobody is emailed and nothing is sent anywhere: the result lives in
the pull request.

**Why bother.** Request redaction is a rule that matches patterns, and rules stop matching. A
provider adds a field, someone refactors a prompt template, a regex gets narrowed to fix a false
positive. None of that fails a normal test suite, because the response still looks right. This
is the check that notices, on the pull request that caused it, while it is still free to fix.

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

**That badge reports the job, not the measurement.** It is green whenever the workflow
exited cleanly, so a gateway that contained everything and one that was never asked to
look both show the same tick. To put the result itself on your README, the run writes a
second file:

```
pii-leak-badge.json
```

It lands in the reports directory beside `summary.md`, so it is already in the artifact the
workflow uploads. It is a [Shields.io endpoint](https://shields.io/badges/endpoint-badge)
file. Publish it anywhere your repository serves raw files, then point Shields at it:

```markdown
[![PII leak check](https://img.shields.io/endpoint?url=https://OWNER.github.io/REPO/pii-leak-badge.json)](https://github.com/OWNER/REPO/actions/workflows/pii-leak-check.yml)
```

The badge then reads `contained`, or `leaked 4 of 16`, or one of the grey outcomes below.
Nothing is hosted by this project and there is nowhere to register: the file comes from your
run and is served by you, which is also why it is not evidence to anyone else. A reader who
wants to check it follows the badge to the run and reruns the instrument named in
`pii-leak-benchmark cite`.

To render one by hand from any finished report:

```bash
pii-leak-benchmark badge ./PII_LEAK_BENCHMARK_LATEST.json --out ./pii-leak-badge.json
```

| Badge reads | Colour | What it means |
| :--- | :--- | :--- |
| `contained` | green | Redaction was on, the run was attributable, nothing reached the upstream |
| `leaked N of M` | red | Protected values were observed upstream |
| `no leak, profile not met` | yellow | Nothing leaked, but some other required check did not hold |
| `inconclusive` | grey | The run could not attribute what it saw |
| `redaction not enabled` | grey | Redaction was off, so the run says nothing about the product |
| `claim unstated` | grey | No redaction claim was recorded, so the run is not a verdict |
| `no redaction offered` | blue | The product does not claim to redact |

Only a real pass is green. A grey badge is not a soft pass: it means the run did not learn
anything about the gateway, and it is worth fixing the configuration and rerunning.

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
- uses: ninadphalak/LLM-Shield-Proxy@benchmark-v0.3.1
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
  - uses: ninadphalak/LLM-Shield-Proxy@benchmark-v0.3.1
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
pip install "pii-leak-benchmark @ git+https://github.com/ninadphalak/LLM-Shield-Proxy@benchmark-v0.3.1#subdirectory=pii-leak-benchmark"
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
