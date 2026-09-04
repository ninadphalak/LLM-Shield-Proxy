# LiteLLM v2 profile run -- RESOLVED via Docker

**Status 2026-09-04: measured.** Results in `../results/v2-response-split/` --
`litellm-presidio.json` and `seed-sweep-litellm.json`.

Run it with `config.docker.yaml`, not `config.yaml`. The pip route was abandoned; see
"Why Docker" below, because the reason matters more than the recipe.

## The recipe that works

```bash
# 1. Shared network so LiteLLM can reach Presidio and Postgres by container name.
docker network create v2profile
docker network connect v2profile presidio-analyzer
docker network connect v2profile presidio-anonymizer

# 2. Postgres. LiteLLM's proxy requires a database for auth in 1.99.
docker run -d --name litellm-pg --network v2profile \
  -e POSTGRES_PASSWORD=litellm -e POSTGRES_USER=litellm -e POSTGRES_DB=litellm \
  -p 5433:5432 postgres:16

# 3. LiteLLM. MSYS_NO_PATHCONV=1 is required under Git Bash on Windows, or the shell
#    rewrites /app/config.yaml into C:/Program Files/Git/app/config.yaml and the
#    container dies with "Config file not found".
MSYS_NO_PATHCONV=1 docker run -d --name litellm-v2 --network v2profile \
  --add-host=host.docker.internal:host-gateway \
  -p 4321:4000 \
  -e DATABASE_URL="postgresql://litellm:litellm@litellm-pg:5432/litellm" \
  -e LITELLM_MASTER_KEY="sk-v2-profile-local" \
  -v "C:/git_repo/LLM-Shield-Proxy/benchmarks/litellm-v2-profile/config.docker.yaml:/app/config.yaml:ro" \
  ghcr.io/berriai/litellm:main-latest --config /app/config.yaml --port 4000

# 4. Wait for "Application startup complete" in `docker logs litellm-v2`, then:
V2_GATEWAY_TOKEN=sk-v2-profile-local python -m pii_leak_benchmark.v2_emitter \
  --validate --only litellm-presidio \
  --gateway-url http://127.0.0.1:4321/v1/chat/completions \
  --upstream-port 8799 --model capture --seed a1b2c3d4e5f60001

# 5. And across seeds:
V2_GATEWAY_TOKEN=sk-v2-profile-local python benchmarks/v2_seed_sweep.py \
  --seeds 6 --only litellm-presidio \
  --gateway-url http://127.0.0.1:4321/v1/chat/completions \
  --upstream-port 8799 --model capture \
  --out benchmarks/results/v2-response-split/seed-sweep-litellm.json
```

The capture runs on the **host** at 8799 and LiteLLM reaches it via
`host.docker.internal`. The emitter starts and stops it per case, so a "Connection error"
from LiteLLM when no run is in flight is expected, not a fault.

## Why Docker, and not pip

Installing `litellm[proxy]` into the harness venv **broke a project invariant**:
`tests/conformance/test_harness_install_weight.py` asserts that importing
`pii_leak_benchmark` pulls in nothing beyond the httpx dependency tree. `httpx/__init__.py`
does a guarded `from ._main import main`, and `_main` imports `click` and `rich` -- so once
the install completed that chain, `import httpx` started dragging them in and three tests
failed. Uninstalling litellm and its dependencies did **not** restore it.

**Rule: software under test does not go in the harness environment.** It gets a container.
That is not a workaround for Windows; it is the correct arrangement, and the Windows
breakage below is a secondary reason.

## Blockers the pip route hit, kept for the record

1. **Startup crashes on Windows cp1252.** LiteLLM prints an ASCII-art banner;
   `click.echo` raises `UnicodeEncodeError` and the app exits. Needs
   `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`. Until set, every other symptom is a red herring
   because the proxy never runs.
2. **Auth requires a database.** No `master_key` gives `401 "No api key passed in"`; any
   key with no DB gives `400 "No connected db."`.
3. **`prisma generate` crashes the same way** and needs `python -X utf8`.
4. **Startup then blocks silently** in "Preparing the Prisma CLI toolchain (timeout
   600.0s)" and never binds. The container image has this pre-baked.

## Environment hazard that cost real time

**Ports 4000-4008 were already held by ~12 LiteLLM proxy processes from a different
session**, running under a different Python. `curl http://127.0.0.1:4000/...` answered, so
setup *looked* successful when this session's proxy had not started at all. Several
intermediate "results" were that other proxy replying.

Those processes were deliberately left running -- they are not this session's to kill. The
container publishes on **4321** to stay clear of them.

**Always confirm your own process owns the port** before trusting a response:
`netstat -ano | grep ":<port> "` and match the PID.
