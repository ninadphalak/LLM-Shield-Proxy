#!/usr/bin/env bash
#
# Re-measure the eight external-gateway rows on the CURRENT instrument.
#
# STAGED, NOT RUN. Trigger it yourself: `bash benchmarks/rerun-external-gateway-rows.sh`
# for all eight, or pass names to select -- e.g.
#   bash benchmarks/rerun-external-gateway-rows.sh litellm portkey
#
# WHY THIS FILE EXISTS
# These eight rows carry no `instrument` block at all and are the last artefacts in
# benchmarks/results/v2-response-split/ that predate the current inspector
# (inspector_sha256 955edd079f406e13). `test_every_artefact_records_the_instrument_that_
# produced_it` is RED because of them, correctly. Re-run them or move them out; do not
# weaken the guard.
#
# TWO RULES THIS SCRIPT ENFORCES, both of them things that have gone wrong before:
#
#   1. --out is ALWAYS explicit. `--validate` WRITES -- it is not a dry run -- and --out
#      defaults to benchmarks/results/v2-response-split, so an invocation without it
#      overwrites the published artefacts it is meant to reproduce. Measured happening.
#      This script writes to a staging directory and NEVER to the published one; promote
#      by hand once you have diffed.
#
#   2. Every row carries the env it was originally measured under. The bearer token,
#      Portkey's header block and V2_REQUEST_PATH_REDACTION are part of the measured
#      configuration, not decoration. Portkey in particular FAILS OPEN: without the
#      x-portkey-config header it answers 200 having applied no guardrail at all, which
#      reads in the report as a gateway that redacts nothing. The values below were read
#      back out of the published artefacts, not retyped from memory.
#
# The seed is the published one, so every row is directly comparable to what it replaces.
#
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

SEED=a1b2c3d4e5f60001
OUT=${OUT:-benchmarks/results/staging-external}          # never the published directory
CAPTURE_PORT=8799
mkdir -p "$OUT"

# Portkey's routing and guardrail config travel as headers. ONE LINE: the value is JSON
# inside JSON, and a line break inside it silently truncates the guardrail block.
PORTKEY_HEADERS='{"x-portkey-provider":"openai","x-portkey-custom-host":"http://host.docker.internal:8799/v1","x-portkey-config":"{\"output_guardrails\":[{\"checks\":[{\"id\":\"portkey.pii\",\"parameters\":{\"redact\":true}}]}]}"}'

run_row() {
  local label=$1 policy=$2 url=$3 model=$4
  local port=${url#*://*:} ; port=${port%%/*}
  if ! (echo > "/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    echo "SKIP  $label -- nothing listening on 127.0.0.1:$port"
    return 0
  fi
  echo "=== $label -> $OUT/$policy.json ==="
  python -m pii_leak_benchmark.v2_emitter --validate \
    --only "$policy" \
    --gateway-url "$url" \
    --upstream-port "$CAPTURE_PORT" \
    --model "$model" \
    --seed "$SEED" \
    --out "$OUT"
}

SELECTED="${*:-litellm portkey nemo shield-on shield-off llm-guard-chunk llm-guard-buffered guardrails}"
want() { case " $SELECTED " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

# ---------------------------------------------------------------------------------------
# Container-backed rows. All five containers were up at the time this file was written;
# `docker ps` to confirm, and see each benchmarks/<target>-v2-profile/STATUS.md to rebuild.
# ---------------------------------------------------------------------------------------

# LiteLLM 1.99 + Presidio guardrail. Needs litellm-pg (Postgres) up as well: LiteLLM's
# proxy requires a database for auth in 1.99.
if want litellm; then
  V2_GATEWAY_TOKEN=sk-v2-profile-local \
  V2_REQUEST_PATH_REDACTION=configured \
  run_row "litellm-presidio" litellm-presidio \
    "http://127.0.0.1:4321/v1/chat/completions" capture
fi

# Portkey OSS gateway. See the fail-open note above before touching PORTKEY_HEADERS.
if want portkey; then
  V2_GATEWAY_TOKEN=sk-dummy \
  V2_GATEWAY_HEADERS="$PORTKEY_HEADERS" \
  V2_REQUEST_PATH_REDACTION=not-configured \
  run_row "portkey-gateway-oss" portkey-gateway-oss \
    "http://127.0.0.1:8788/v1/chat/completions" capture
fi

# NeMo Guardrails 0.24.0. Model is `config`, not `capture` -- it routes on config id.
if want nemo; then
  V2_REQUEST_PATH_REDACTION=not-configured \
  run_row "nemo-guardrails-0.24.0" nemo-guardrails-0.24.0 \
    "http://127.0.0.1:9001/v1/chat/completions" config
fi

# LLM-Shield-Proxy 1.6.0, response rehydration ON (8813) and OFF (8814).
if want shield-on; then
  V2_GATEWAY_TOKEN=sk-shield-v2-profile \
  V2_REQUEST_PATH_REDACTION=configured \
  run_row "llm-shield-proxy-1.6.0-response-on" llm-shield-proxy-1.6.0-response-on \
    "http://127.0.0.1:8813/v1/chat/completions" capture
fi

if want shield-off; then
  V2_GATEWAY_TOKEN=sk-shield-v2-profile \
  V2_REQUEST_PATH_REDACTION=configured \
  run_row "llm-shield-proxy-1.6.0-response-off" llm-shield-proxy-1.6.0-response-off \
    "http://127.0.0.1:8814/v1/chat/completions" capture
fi

# ---------------------------------------------------------------------------------------
# Library rows. These are NOT containers -- they are two wrapper gateways run under a
# Python 3.10-3.12 interpreter, because llm-guard==0.3.16 declares requires_python
# <3.13 and pins torch, and this harness runs CPython 3.14. They cannot share an
# environment; software under test does not go in the harness environment.
#
# At the time of writing all three were ALREADY RUNNING under
# C:\Users\ninad\AppData\Local\Programs\Python\Python312\python.exe -- no venv involved.
# If they are not, start them first (each backgrounds itself and keeps running):
#
#   py -3.12 -m venv venv-llmguard
#   venv-llmguard/Scripts/pip install llm-guard==0.3.16      # multi-GB: torch, transformers
#   LLMGUARD_MODE=chunk-local venv-llmguard/Scripts/python \
#     benchmarks/llm-guard-v2-profile/gateway.py --port 8790 &
#   LLMGUARD_MODE=buffered    venv-llmguard/Scripts/python \
#     benchmarks/llm-guard-v2-profile/gateway.py --port 8792 &
#
#   py -3.12 -m venv venv-guardrails
#   venv-guardrails/Scripts/pip install guardrails-ai==0.10.2   # pulls litellm + openai
#   venv-guardrails/Scripts/python \
#     benchmarks/guardrails-v2-profile/gateway.py --port 8791 &
#
# LLMGUARD_MODE is read from the gateway's OWN environment at start-up and is invisible
# from the outside, so 8790 must have been started with chunk-local and 8792 with
# buffered. If you cannot account for how a listener was started, restart it rather than
# measure it -- a row naming a mode the process was not in is unfalsifiable.
# ---------------------------------------------------------------------------------------

if want llm-guard-chunk; then
  V2_REQUEST_PATH_REDACTION=configured \
  run_row "llm-guard-chunk-local (expects LLMGUARD_MODE=chunk-local on :8790)" \
    llm-guard-chunk-local "http://127.0.0.1:8790/v1/chat/completions" capture
fi

if want llm-guard-buffered; then
  V2_REQUEST_PATH_REDACTION=configured \
  run_row "llm-guard-buffered (expects LLMGUARD_MODE=buffered on :8792)" \
    llm-guard-buffered "http://127.0.0.1:8792/v1/chat/completions" capture
fi

# Guardrails AI 0.10.2. `not-configured`: it has no restore half, so its FidelityRate 0.00
# is a real failure of a thing it does not claim to do -- never score it for rehydration.
if want guardrails; then
  V2_REQUEST_PATH_REDACTION=not-configured \
  run_row "guardrails-ai-stream-validate" guardrails-ai-stream-validate \
    "http://127.0.0.1:8791/v1/chat/completions" capture
fi

cat <<'NOTE'

Done. Rows are in the staging directory -- NOT published.

Before promoting any of them:
  * Check `cases_inconclusive` is 0. A gateway that refused every case reports all four
    rates as 0.00, which reads as a flawless gateway; that has happened here before.
  * Diff against the row it replaces. A moved rate is a finding and needs a sentence in
    the README, not a silent overwrite.
  * Confirm `instrument.inspector_sha256` matches `inspector_digest()`.

Then copy into benchmarks/results/v2-response-split/ and run
  python -m pytest tests/conformance/test_results_are_comparable.py
NOTE
