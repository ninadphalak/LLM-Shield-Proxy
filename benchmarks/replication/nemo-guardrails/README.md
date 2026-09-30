# Replication pack: NeMo Guardrails 0.24.0 with its Presidio output rail

Reproduces the `nemo-guardrails-0.24.0` row: NeMo Guardrails serving the config in
`config/`, with `detect sensitive data on output` (Presidio, in NeMo's own process) as
its output rail and the harness capture as its model. You need Docker and this checkout.
No API key, no model, nothing installed on the host.

```bash
cd benchmarks/replication/nemo-guardrails
docker compose up --build --exit-code-from runner
```

The first run builds the NeMo image, which downloads the pinned packages and the
`en_core_web_lg` model (about 600 MB); after that, about six minutes. The run ends with
the reports under `./out/` and this line from `runner`:

```text
nemo-guardrails-0.24.0: 6 seeds, fidelity 0, leak(1chunk) 0.0556 [0-0.1667], leak(adv) 0.0556 [0-0.1667], DeltaFrag 0, request-path leak CARDPAN,EMAIL,SSN,USPHONE, outcome fail, inspector 94262e29a492ab6a: MATCHES the published row; wall seed a1b2c3d4e5f60001 matches
```

Exit code 0 means every compared number matched; 1 lists the differences and writes
them to `out/comparison.json`; 2 means NeMo never answered and nothing was measured.

## The published numbers this checks against

`benchmarks/results/v2-response-split/seed-sweep-nemo.json`, six seeds
`0000000000000001` to `0000000000000006`, harness `0.2.1`, inspector `94262e29a492ab6a`:

| Metric | Mean | Range |
| :--- | ---: | :--- |
| FidelityRate | 0.0 | 0.0 to 0.0 |
| LeakRate, single chunk | 0.0556 | 0.0 to 0.1667 |
| LeakRate, adversarial | 0.0556 | 0.0 to 0.1667 |
| DeltaFrag | 0.0 | 0.0 to 0.0 |

Every seed has 8 of 32 cases inconclusive: NeMo answers HTTP 422 to the request site
those cases use, so the rates are over the 24 applicable cases. Seeds 1 and 2 leak 2 of
12 in each condition; seeds 3 to 6 leak none. Every seed reads `fail` with all four
entity types (`CARDPAN`, `EMAIL`, `SSN`, `USPHONE`) reaching the capture on the request
path: this configuration has no request-path rail, and `V2_REQUEST_PATH_REDACTION` is
`not-configured` to say so.

The runner also replays the wall row's own seed, `a1b2c3d4e5f60001`, and compares the
report to `nemo-guardrails-0.24.0.json`: outcome, the four rates, the case counts (24
applicable, 8 inconclusive), the leaked entity types, the per-condition leak counts (0 of
12 and 0 of 12) and the instrument.

The runner compares each seed's four rates, case counts, outcome and request-path leak,
the summary statistics, and the instrument digests. It does not compare timings,
timestamps or the host environment, which vary by machine.

## What is pinned, and what is not

| Part | Pinned to | Note |
| :--- | :--- | :--- |
| NeMo image | `Dockerfile` here | `benchmarks/nemo-v2-profile/Dockerfile` is the build the row ran and pins nothing. The versions here (`nemoguardrails[server]==0.24.0`, `presidio-analyzer==2.2.364`, `presidio-anonymizer==2.2.364`, `spacy==3.8.16`, `en_core_web_lg` 3.8.0 by wheel hash) were read out of that built image with `pip freeze`, and the base is the digest behind `python:3.11-slim` on the machine that built it. |
| Transitive packages | not pinned | Whatever pip resolves under the pins above. The published image resolved `fastapi==0.141.1` and `pydantic==2.13.5`; the verified run below resolved its own set. |
| NeMo config | `config/config.yml` | Byte-identical to `benchmarks/nemo-v2-profile/config/config.yml`; a test fails if it drifts. |
| Harness | this checkout's `pii-leak-benchmark/` | The runner image is built from it. The line names the inspector digest; `94262e29a492ab6a` is the one the row was scored with. |
| Environment | `V2_REQUEST_PATH_REDACTION=not-configured`, `V2_CLIENT_READ_TIMEOUT=30` | Part of the measured configuration, from the published sweep recipe: NeMo loads a model on its first requests, so the sweep used a 30 s read deadline rather than the 10 s default. |

Not pinned: the runner's `python:3.12-slim` base. The published row was measured from a
Windows host under CPython 3.14; the harness records the environment but no compared
number depends on it.

## How the containers reach each other

NeMo's config points its model at `http://host.docker.internal:8799/v1`, the harness
capture, which in the published run was on the host. Here the runner shares NeMo's
network namespace (`network_mode: service:nemo`) and binds the capture on
`127.0.0.1:8799`, and `extra_hosts` maps `host.docker.internal` to `127.0.0.1` inside
NeMo's container. The config file is therefore used unchanged. The runner reaches NeMo
at `127.0.0.1:9000`, so the reports' `target.base_url` differs from the published one
(`127.0.0.1:9001`, a host port) and nothing else.

## Verified

Run on 2026-09-30 on Windows 11 with Docker 29.7.2 and Compose v5.3.1, image built from this
Dockerfile: all six seeds and the wall seed matched, exit code 0.

Git Bash on Windows: `docker compose` reads the compose file's paths itself, so
`MSYS_NO_PATHCONV` is not needed for this command.
