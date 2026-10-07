# Replication packs

One directory per published results-wall row that can be stood up in containers. Each
pack reproduces the row in one command:

```bash
cd benchmarks/replication/<gateway>
docker compose up --build --exit-code-from runner
```

A pack holds three things:

- `docker-compose.yml`: the gateway, its detector and the harness runner, every pulled
  image pinned by digest.
- The gateway configuration the published row used, copied from the row's profile
  directory. `tests/conformance/test_replication_packs.py` fails if the copy drifts.
- `README.md`: the command, the published numbers the run is checked against, and what
  the pack cannot pin.

`runner/` is shared. Its image is built from this checkout's `pii-leak-benchmark/`, so a
pack measures with the harness revision you checked out, and `replicate.py` replays the
published seeds, rebuilds the sweep file in the shape `v2_seed_sweep.py` writes, and
compares every number to the committed one. The run ends with the reports under the
pack's `out/` (never committed) and one line: the six-seed numbers and whether they match.
Exit code 0 is a match, 1 lists the differences, 2 means the gateway never answered.

| Pack | Row | Verified |
| :--- | :--- | :--- |
| `litellm-presidio` | LiteLLM 1.99 with its Presidio guardrail | 2026-09-30, six seeds and the wall seed matched |
| `nemo-guardrails` | NeMo Guardrails 0.24.0 with its Presidio output rail | 2026-09-30, six seeds and the wall seed matched |

Not every row can be a pack. Rows measured through a hosted service need an account,
and rows measured from a library wrapper need a Python the wrapper supports; those keep
their run recipes in `benchmarks/<gateway>-v2-profile/STATUS.md`.
