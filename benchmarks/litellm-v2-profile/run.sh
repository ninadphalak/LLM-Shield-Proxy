#!/usr/bin/env bash
# Run the v2 response-split profile against a real LiteLLM proxy.
#
# Unlike the reference policies, nothing here is modelled: LiteLLM does the masking, calls
# the harness capture as its configured upstream, and applies its own Presidio guardrail to
# the response. The harness only chooses where the response fragments and then inspects what
# reached the client.
#
# Prerequisites:
#   - presidio-analyzer on 127.0.0.1:5002 and presidio-anonymizer on 127.0.0.1:5001
#   - litellm installed (pip install "litellm[proxy]")
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PORT="${LITELLM_PORT:-4000}"
UPSTREAM_PORT=8799

cd "$ROOT"

echo "== checking Presidio =="
curl -sS -m 10 -X POST "http://127.0.0.1:5002/analyze" \
  -H "Content-Type: application/json" \
  -d '{"text":"a@b.com","language":"en"}' >/dev/null
echo "   analyzer ok"

echo "== starting LiteLLM proxy on :$PORT =="
litellm --config "$HERE/config.yaml" --port "$PORT" --detailed_debug \
  > "$HERE/litellm.log" 2>&1 &
LITELLM_PID=$!
trap 'kill $LITELLM_PID 2>/dev/null || true' EXIT

for _ in $(seq 1 60); do
  if curl -sS -m 2 "http://127.0.0.1:$PORT/health/liveliness" >/dev/null 2>&1; then
    echo "   proxy up"
    break
  fi
  sleep 2
done

echo "== running the v2 profile against LiteLLM =="
export V2_GATEWAY_TOKEN=sk-v2-profile-local
python -m pii_leak_benchmark.v2_emitter \
  --validate \
  --only litellm-presidio \
  --gateway-url "http://127.0.0.1:$PORT/v1/chat/completions" \
  --upstream-port "$UPSTREAM_PORT" \
  --model capture \
  --seed "${SEED:-a1b2c3d4e5f60001}" \
  --out benchmarks/results/v2-response-split

echo "== done; LiteLLM log at $HERE/litellm.log =="
