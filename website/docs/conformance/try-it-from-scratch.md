---
sidebar_position: 4
title: Try it from scratch
description: Reproduce the LLM-Shield-Proxy and LiteLLM results yourself, starting with nothing installed and no API key.
---

# Try it from scratch

You have nothing installed, no gateway running, and no OpenAI key. This page gets you from
there to two measured results you can compare: ours, and LiteLLM with its PII redaction
switched on.

**About 20 minutes.** Nothing here costs money and nothing calls a real model.

## What you are about to do

The benchmark plays the part of two things at once. It pretends to be **your application**
sending a prompt full of fake personal data, and it pretends to be **the model provider**
receiving whatever the gateway forwards. The gateway under test sits in the middle,
configured to redact.

```
  benchmark                gateway under test              benchmark
 (your app) ───prompt───▶  (redacts, or doesn't) ───▶  (stands in for OpenAI)
                                                       and writes down what arrived
```

Because the benchmark is on both ends, it can see exactly what the gateway let through. No
real provider is involved, which is why you need no key and pay nothing.

The test data is fake, generated fresh from a seed. It is never anyone's real information.

## Before you start

You need **Python 3.9 or newer** and **Docker**. Check:

```bash
python --version
docker --version
```

If either is missing, install Python from python.org and Docker Desktop from docker.com,
then come back.

## Step 1: Install the benchmark

```bash
python -m pip install pii-leak-benchmark
```

That is the whole install. It pulls in one library and nothing else, on purpose: you should
not have to install a gateway in order to measure a different gateway.

Check it arrived:

```bash
pii-leak-benchmark --help
```

## Step 2: Prove the benchmark can catch a leak

Before measuring anything real, measure something you already know the answer to. This runs
the check against a target that does no redaction at all:

```bash
pii-leak-benchmark selfcheck --target-base-url capture://self
```

Every data type should come back as a leak. **That is the correct result** and it is the
point: it shows the instrument is working. If this came back clean, every later result
would be worthless, because a benchmark that cannot see a leak will call anything clean.

## Step 3: Measure LLM-Shield-Proxy

Start ours, pointed at the benchmark's capture address as its upstream:

```bash
docker run -d --name shield -p 8000:8000 \
  --add-host=host.docker.internal:host-gateway \
  -e UPSTREAM_BASE_URL=http://host.docker.internal:8765/v1 \
  -e OPENAI_API_KEY=sk-not-used-by-the-capture \
  ghcr.io/ninadphalak/llm-shield-proxy:latest
```

`UPSTREAM_BASE_URL` is the important line. It tells the gateway to send its traffic to the
benchmark instead of to OpenAI, which is what lets the benchmark see what was forwarded.
The key is never used by anything: the capture accepts whatever it is given.

Wait for it to come up, then measure:

```bash
pii-leak-benchmark selfcheck --target-base-url http://localhost:8000/v1 \
  --json-out shield.json
```

Read the `Checks` block it prints. `configured_upstream_boundary: pass` means nothing you
sent reached the capture. `response_fidelity: pass` means your own data came back intact.

Stop it when you are done:

```bash
docker rm -f shield
```

## Step 4: Measure LiteLLM with redaction on

This one takes more setup, and the reason is worth knowing before you start: LiteLLM does
not detect PII itself. It calls out to Microsoft Presidio, and its proxy wants a database
for authentication. So there are four containers rather than one. None of this is a
criticism of the product, it is just what the thing needs to run with redaction switched
on, and a run without redaction switched on measures nothing.

A shared network first, so the containers can find each other by name:

```bash
docker network create bench
```

Presidio, which is the part that actually recognises an email address:

```bash
docker run -d --name presidio-analyzer --network bench -p 5002:3000 \
  mcr.microsoft.com/presidio-analyzer:latest
docker run -d --name presidio-anonymizer --network bench -p 5001:3000 \
  mcr.microsoft.com/presidio-anonymizer:latest
```

A database, because the LiteLLM proxy will answer 401 to everything without one:

```bash
docker run -d --name litellm-db --network bench \
  -e POSTGRES_PASSWORD=litellm -e POSTGRES_USER=litellm -e POSTGRES_DB=litellm \
  postgres:16
```

Now a file called `litellm.yaml`:

```yaml
model_list:
  - model_name: conformance-model
    litellm_params:
      model: openai/conformance-model
      api_base: http://host.docker.internal:8765/v1
      api_key: sk-not-used-by-the-capture

guardrails:
  - guardrail_name: presidio-mask
    litellm_params:
      guardrail: presidio
      mode: pre_call
      output_parse_pii: true
      presidio_analyzer_api_base: http://presidio-analyzer:3000
      presidio_anonymizer_api_base: http://presidio-anonymizer:3000
      default_on: true

litellm_settings:
  drop_params: true

general_settings:
  master_key: sk-local-only
```

The `guardrails` block is the part that matters. Without it you are measuring a plain
relay.

And LiteLLM itself:

```bash
docker run -d --name litellm --network bench -p 4000:4000 \
  --add-host=host.docker.internal:host-gateway \
  -e DATABASE_URL="postgresql://litellm:litellm@litellm-db:5432/litellm" \
  -e LITELLM_MASTER_KEY="sk-local-only" \
  -v "$(pwd)/litellm.yaml:/app/config.yaml:ro" \
  ghcr.io/berriai/litellm:main-latest --config /app/config.yaml --port 4000
```

:::note Windows, Git Bash
Put `MSYS_NO_PATHCONV=1` in front of that last command. Without it the shell rewrites
`/app/config.yaml` into a Windows path and the container exits with "Config file not
found".
:::

Wait for `Application startup complete` in `docker logs litellm`, then run the same check
against the other port:

```bash
pii-leak-benchmark selfcheck --target-base-url http://localhost:4000/v1 \
  --target-api-key sk-local-only \
  --json-out litellm.json
```

Clean up:

```bash
docker rm -f litellm litellm-db presidio-analyzer presidio-anonymizer
docker network rm bench
```

## Step 5: Compare the two

You now have `shield.json` and `litellm.json`, measured by the same instrument, with the
same fake data, on the same machine, minutes apart. That last part is why this is worth
doing yourself rather than reading our table: a result you produced has none of our
fingerprints on it.

The fields worth reading in each file:

| Field | What it answers |
| :--- | :--- |
| `checks.configured_upstream_boundary.leaked_entity_types` | Which data types reached the provider |
| `checks.configured_upstream_boundary.correlated_requests` | Whether anything arrived at all |
| `checks.response_fidelity.passed` | Whether the caller got their own data back |

A gateway can fail either one on its own. Sending nothing upstream but returning nothing
useful is not a pass, and neither is a perfect response that leaked the caller's data on
the way out.

`pii-leak-benchmark cite shield.json` prints a short block naming the harness version and
the exact test data, so a third person can repeat what you just did.

:::note Why `selfcheck` and not the plain command
`selfcheck` measures your deployment and says so. The flat `pii-leak-benchmark` command is
the one that produces a **publishable** row, and it requires you to record what the vendor
claims about redaction and where they claim it, because a number published next to a
product name without that context is not a fair comparison. See
[submitting a result](./submitting) for the flags.
:::

## If something goes wrong

**Everything says `inconclusive`, or `correlated_requests` is 0.** The gateway never
reached the benchmark, so nothing was measured. The usual cause is the upstream address:
inside Docker, `localhost` means the container, not your machine. Use
`host.docker.internal` as shown above.

**LiteLLM will not start.** Run `docker logs litellm`. A config it cannot parse is the most
common reason, and it says so.

**The benchmark says the port is busy.** Something else is on 8765. Pass
`--capture-port 9000` and try again.

**LiteLLM answers 401.** The database was not up when it started, or the key does not
match `master_key`. Restart it after `docker logs litellm-db` shows Postgres ready.

**Results differ from our published table.** That is interesting and we want to hear about
it. Versions move, defaults change, and a disagreement usually means a real difference
rather than a mistake. [Open a question](https://github.com/ninadphalak/LLM-Shield-Proxy/issues/new?template=result-dispute.yml)
and it appears next to the row it disputes.

## Publish what you found

If you ran this in CI rather than on your laptop, your result can go on the
[results wall](./who-has-run-it) by itself:

```bash
pii-leak-benchmark submit
```

Every measured number is read from your CI run's own build artifact, so nothing is retyped.
See [the CI setup](./ci) for the workflow file.

## What this does and does not tell you

It tells you what these versions of these two gateways, in these configurations, did with
this fake data on your machine today.

It does not tell you which product is better. Each result is one version in one
configuration, a gateway that fails one data type may handle everything else, and a leak
count can mean two different bugs, which is why
[the results wall is not a scoreboard](./who-has-run-it#two-bugs).
