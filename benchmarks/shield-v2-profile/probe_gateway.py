"""Print both sides of one v2 request through an external gateway.

FidelityRate 0.0 has two possible causes that mean opposite things:
  (a) the gateway masked the prompt and never restored it -> a finding about the gateway
  (b) the harness never presented anything to restore     -> a bug in the harness
Only the prompt the upstream actually received tells them apart, so this prints it.

Usage:
  python benchmarks/shield-v2-profile/probe_gateway.py --url URL --token TOK [--model M]
"""
import argparse
import json
import sys
from urllib.request import Request, urlopen

sys.path.insert(0, "pii-leak-benchmark")
from pii_leak_benchmark.v2_emitter import (  # noqa: E402
    UpstreamState,
    _make_upstream,
    _serve,
    build_segments,
)

ap = argparse.ArgumentParser()
ap.add_argument("--url", required=True)
ap.add_argument("--token", default="")
ap.add_argument("--model", default="capture")
ap.add_argument("--seed", default="a1b2c3d4e5f60001")
ap.add_argument("--upstream-port", type=int, default=8799)
args = ap.parse_args()

segments = build_segments(args.seed)
case = {"entity": "EMAIL", "encoding": "plain",
        "fragmentation": "adversarial", "carrier": "sse-delta-content"}
state = UpstreamState(segments=segments, case=case)
server, _url = _serve(_make_upstream(state), port=args.upstream_port)

prompt = "Please review: " + ", ".join(segments.echo.values())
body = json.dumps({"model": args.model, "stream": True,
                   "messages": [{"role": "user", "content": prompt}]}).encode()
headers = {"Content-Type": "application/json"}
if args.token:
    headers["Authorization"] = f"Bearer {args.token}"
try:
    with urlopen(Request(args.url, data=body, headers=headers), timeout=180) as r:
        sse = r.read().decode("utf-8", "replace")
        status = r.status
except Exception as exc:  # noqa: BLE001
    sse, status = f"<request failed: {exc!r}>", "ERR"
finally:
    server.shutdown()

print(f"HTTP {status}")
print("CLIENT SENT (echo values) :", segments.echo)
print("NEVER SENT (injection)    :", segments.injection)
print()
received = state.received_bodies[0] if state.received_bodies else "<upstream got nothing>"
try:
    up_prompt = json.loads(received)["messages"][0]["content"]
except Exception:  # noqa: BLE001
    up_prompt = received
print("PROMPT THE UPSTREAM RECEIVED (what the gateway forwarded):")
print("   ", up_prompt[:400])
print()
chunks, events = [], 0
for line in sse.splitlines():
    if line.startswith("data: "):
        events += 1
        d = line[6:]
        if d != "[DONE]":
            try:
                chunks.append(json.loads(d)["choices"][0]["delta"].get("content", ""))
            except Exception:  # noqa: BLE001
                pass
client_text = "".join(chunks)
print(f"CLIENT SAW ({events} SSE events):")
print("   ", (client_text or sse)[:600])
print()
for k, v in segments.echo.items():
    print(f"  echo {k:8} original restored to client? {v in client_text}")
for k, v in segments.injection.items():
    print(f"  inj  {k:8} leaked to client?             {v in client_text}")
