# LiteLLM v2 profile run — BLOCKED, not attempted-and-failed

**Status 2026-09-04: no LiteLLM measurement exists.** Nothing in
`benchmarks/results/v2-response-split/` came from LiteLLM. This file records how far the
setup got and exactly what stopped it, so the next attempt does not rediscover it.

The config and runner here are believed correct but **unverified against a running proxy**.
Do not cite them as a result.

## What works

- `litellm` 1.99.0 installed (`pip install "litellm[proxy]"`).
- `prisma` 0.15.0 installed, and the client generates cleanly:
  ```
  PYTHONUTF8=1 DATABASE_URL=postgresql://litellm:litellm@127.0.0.1:5433/litellm \
    python -X utf8 -m prisma generate --schema=venv/Lib/site-packages/litellm/proxy/schema.prisma
  ```
- Postgres 16 for LiteLLM: `docker run -d --name litellm-pg -e POSTGRES_PASSWORD=litellm
  -e POSTGRES_USER=litellm -e POSTGRES_DB=litellm -p 5433:5432 postgres:16`
- Presidio analyzer/anonymizer containers on 5002/5001 (already running; these ARE used by
  the measured `presidio-*` rows).

## Four blockers hit, in order

1. **Startup crashes on Windows cp1252.** LiteLLM prints an ASCII-art banner at startup;
   `click.echo` raises `UnicodeEncodeError: 'charmap' codec can't encode characters` and the
   app exits with `Application startup failed`. **Fix: `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`.**
   Until this is set, every other symptom below is a red herring, because the proxy is not
   running at all.

2. **Auth requires a database.** With no `master_key`, requests get
   `401 "No api key passed in"`. With a `master_key`, any key — including the master key
   itself — returns `400 {"error":{"message":"No connected db."}}`. So a DB is mandatory for
   the proxy path in 1.99.0.

3. **`prisma generate` also crashes on cp1252** for the same reason as (1); it needs
   `python -X utf8`. Without it: `UnicodeEncodeError ... '→'`.

4. **Startup then blocks silently in "Preparing the Prisma CLI toolchain (timeout 600.0s)"**
   and never binds the port. Observed for >100 s in the foreground with zero output, and
   0 bytes of log across several background attempts. **This is where it stands.** It may
   simply need a longer wait on first run while the Prisma engines download, or it may be
   failing silently.

## Environment hazard that cost real time

**Ports 4000-4008+ are already held by ~12 LiteLLM proxy processes from a different
session** (running under `Python312`, not this repo's venv). Two consequences:

- `curl http://127.0.0.1:4000/...` answers, so it *looks* like your proxy started when it
  did not. Several "results" during setup were that other proxy replying.
- **Those processes were deliberately left alone.** They are not this session's to kill and
  may be someone's running work. Use a port outside 4000-4010 (4321 was free).

Always confirm your own process owns the port before trusting a response:
`netstat -ano | grep ":<port> "` and match the PID.

## Next attempt

1. Start the proxy and **wait out the Prisma toolchain step** — up to 10 minutes on a cold
   run — before concluding it is broken.
2. Confirm `netstat` shows your PID on the port.
3. Then: `bash benchmarks/litellm-v2-profile/run.sh` (it sets `V2_GATEWAY_TOKEN` and passes
   `--gateway-url`, `--upstream-port 8799`, `--model capture`).

A Linux or WSL host avoids blockers (1) and (3) entirely and is the faster route.

## What the run would add

The `presidio-*` rows already measured are a real **detector** inside a wrapper written
here. LiteLLM would be the first real **gateway product** — its own masking, its own
`output_parse_pii` return path, its own streaming behaviour. That is the difference between
"the bug class reproduces in a detector" and "the bug class reproduces in a shipping
gateway", and it is the single highest-value remaining measurement.
