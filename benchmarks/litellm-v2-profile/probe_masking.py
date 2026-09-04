"""Decisive check: did LiteLLM mask on the way out, and rehydrate on the way back?

FidelityRate 0.0 has two possible causes and they mean opposite things:
  (a) LiteLLM masked the prompt and never restored it  -> a real finding
  (b) the harness never presented anything to restore  -> a harness bug
This prints the prompt the upstream actually received and the text the client got, so the
two can be told apart by eye.
"""
import json
import sys
import threading
from urllib.request import Request, urlopen

sys.path.insert(0, 'pii-leak-benchmark')
from pii_leak_benchmark.v2_emitter import (  # noqa: E402
    UpstreamState,
    _make_upstream,
    _serve,
    build_segments,
)

segments = build_segments("a1b2c3d4e5f60001")
case = {"entity": "EMAIL", "encoding": "plain",
        "fragmentation": "adversarial", "carrier": "sse-delta-content"}
state = UpstreamState(segments=segments, case=case)
server, _url = _serve(_make_upstream(state), port=8799)

prompt = "Please review: " + ", ".join(segments.echo.values())
body = json.dumps({"model": "capture", "stream": True,
                   "messages": [{"role": "user", "content": prompt}]}).encode()
req = Request("http://127.0.0.1:4321/v1/chat/completions", data=body,
              headers={"Content-Type": "application/json",
                       "Authorization": "Bearer sk-v2-profile-local"})
try:
    with urlopen(req, timeout=180) as r:
        sse = r.read().decode("utf-8", "replace")
finally:
    server.shutdown()

print("ECHO values the client sent :", segments.echo)
print("INJECTED values (never sent):", segments.injection)
print()
received = state.received_bodies[0] if state.received_bodies else "<upstream got nothing>"
try:
    up_prompt = json.loads(received)["messages"][0]["content"]
except Exception:
    up_prompt = received
print("PROMPT THE UPSTREAM RECEIVED (what LiteLLM sent on):")
print("   ", up_prompt[:300])
print()
chunks = []
events = 0
for line in sse.splitlines():
    if line.startswith("data: "):
        events += 1
        d = line[6:]
        if d != "[DONE]":
            try:
                chunks.append(json.loads(d)["choices"][0]["delta"].get("content", ""))
            except Exception:
                pass
client_text = "".join(chunks)
print(f"CLIENT SAW ({events} SSE events):")
print("   ", client_text[:400])
print()
for k, v in segments.echo.items():
    print(f"  echo {k:8} original present in client text? {v in client_text}")
for k, v in segments.injection.items():
    print(f"  inj  {k:8} leaked to client?                {v in client_text}")
