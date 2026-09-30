# Replication pack: LiteLLM 1.99 with its Presidio guardrail

Reproduces the `litellm-presidio` row: LiteLLM's proxy with the `presidio` guardrail on
(`mode: pre_call`, `output_parse_pii: true`), Presidio as its detector, and the harness
capture as its configured upstream. You need Docker and this checkout. No API key, no
model, nothing installed on the host.

```bash
cd benchmarks/replication/litellm-presidio
docker compose up --build --exit-code-from runner
```

About five minutes: LiteLLM takes a minute to start, then seven harness runs of 32
cases each. The run ends with the reports under `./out/` and this line from `runner`:

```text
litellm-presidio: 6 seeds, fidelity 0, leak(1chunk) 0.0625 [0-0.1875], leak(adv) 0.0625 [0-0.1875], DeltaFrag 0, request-path leak CARDPAN,EMAIL,SSN,USPHONE, outcome fail, inspector 94262e29a492ab6a: MATCHES the published row; wall seed a1b2c3d4e5f60001 matches
```

Exit code 0 means every compared number matched; 1 lists the differences and writes
them to `out/comparison.json`; 2 means LiteLLM never answered and nothing was measured.

## The published numbers this checks against

`benchmarks/results/v2-response-split/seed-sweep-litellm.json`, six seeds
`0000000000000001` to `0000000000000006`, harness `0.2.1`, inspector `94262e29a492ab6a`:

| Metric | Mean | Range |
| :--- | ---: | :--- |
| FidelityRate | 0.0 | 0.0 to 0.0 |
| LeakRate, single chunk | 0.0625 | 0.0 to 0.1875 |
| LeakRate, adversarial | 0.0625 | 0.0 to 0.1875 |
| DeltaFrag | 0.0 | 0.0 to 0.0 |

Seeds 1 and 2 leak 3 of 16 cases in each condition; seeds 3 to 6 leak none. Every seed
reads `fail` with all four entity types (`CARDPAN`, `EMAIL`, `SSN`, `USPHONE`) reaching
the capture on the request path, which is what the results-wall row reports as "all 4
types sent" and "none restored".

The runner also replays the wall row's own seed, `a1b2c3d4e5f60001`, and compares the
report to `litellm-presidio.json`: outcome, the four rates, the case counts, the leaked
entity types, the per-condition leak counts (0 of 16 and 0 of 16) and the instrument.

The runner compares each seed's four rates, case counts, outcome and request-path leak,
the summary statistics, and the instrument digests. It does not compare timings,
timestamps or the host environment, which vary by machine.

## What is pinned, and what is not

| Part | Pinned to | Note |
| :--- | :--- | :--- |
| LiteLLM | `ghcr.io/berriai/litellm@sha256:570a872d...` | 1.99.0. The run recipe named `main-latest`, which resolved to this image when the row was measured; the wall row says 1.99 and `litellm-source-reproduction.yml` pins the same digest. |
| Presidio analyzer | `mcr.microsoft.com/presidio-analyzer@sha256:286e3fa7...` | The digest behind `latest` on the machine that measured the row. |
| Presidio anonymizer | `mcr.microsoft.com/presidio-anonymizer@sha256:a10a12a2...` | Same. |
| Postgres | `postgres@sha256:f1c3376c...` | The digest behind `16` on that machine. LiteLLM 1.99 needs a database for auth; it holds nothing the run reads. |
| LiteLLM config | `config.docker.yaml` | Byte-identical to `benchmarks/litellm-v2-profile/config.docker.yaml`; a test fails if it drifts. |
| Harness | this checkout's `pii-leak-benchmark/` | The runner image is built from it. The line names the inspector digest; `94262e29a492ab6a` is the one the row was scored with. |
| Environment | `V2_GATEWAY_TOKEN`, `V2_REQUEST_PATH_REDACTION=configured` | Part of the measured configuration, from the published run recipe. |

Not pinned: the runner's `python:3.12-slim` base. The published row was measured from a
Windows host under CPython 3.14; the harness records the environment but no compared
number depends on it.

## How the containers reach each other

LiteLLM's config points its model at `http://host.docker.internal:8799/v1`, the harness
capture, which in the published run was on the host. Here the runner shares LiteLLM's
network namespace (`network_mode: service:litellm`) and binds the capture on
`127.0.0.1:8799`, and `extra_hosts` maps `host.docker.internal` to `127.0.0.1` inside
LiteLLM's container. The config file is therefore used unchanged. The runner reaches
LiteLLM at `127.0.0.1:4000`, so the reports' `target.base_url` differs from the published
one (`127.0.0.1:4321`, a host port) and nothing else.

## Verified

Run on 2026-09-30 on Windows 11 with Docker 29.7.2 and Compose v5.3.1: all six seeds and
the wall seed matched, exit code 0.

Git Bash on Windows: `docker compose` reads the compose file's paths itself, so
`MSYS_NO_PATHCONV` is not needed for this command.
