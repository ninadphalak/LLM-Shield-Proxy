"""Does the request-path walk cover fields the v2 corpus never uses, and does the
response path restore them?

The v2 corpus puts every protected value in `messages[0].content`. Deep payload
redaction is on by default and walks the whole request body, and an MCP / JSON-RPC
caller routinely carries values in places a chat schema has no name for: `system`, a
tool argument, a nested `metadata` object, a brand-new top-level key. If masking covers
those but rehydration does not, a caller gets a surrogate back and never learns.

So this asks two separate questions per field, and they have different answers:
  1. was the value masked before it reached the upstream?   (request path)
  2. did the original come back to the client?              (response path)

The capture echoes `messages[0].content` and nothing else, so restoration is only
observable for a value that appears THERE. A single exchange would therefore report
"not restored" for every value that happened to live elsewhere, which says nothing about
the gateway. So each value gets its own exchange with itself in the echoed slot, and the
two questions are reported against different denominators:

  masking     per FIELD, from the body the upstream received
  restoration per VALUE, from the run where that value sat in messages[0].content
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
    _stop,
    build_segments,
)

ap = argparse.ArgumentParser()
ap.add_argument("--url", required=True)
ap.add_argument("--token", default="")
ap.add_argument("--upstream-port", type=int, default=8799)
ap.add_argument("--seed", default="a1b2c3d4e5f60001")
args = ap.parse_args()

segments = build_segments(args.seed)
email = segments.echo["EMAIL"]
ssn = segments.echo["SSN"]

# One payload, the same needle in several places. Named after where it sits, so the
# output says which shapes are covered rather than just pass/fail overall.
payload = {
    "model": "capture",
    "stream": True,
    "messages": [
        {"role": "system", "content": f"Operator contact is {email}"},
        {"role": "user", "content": f"Please review: {ssn}"},
    ],
    "metadata": {"nested": {"note": f"ticket raised by {email}"}},
    "an_unrecognised_key": f"jsonrpc-style field carrying {ssn}",
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "lookup_customer",
                "description": f"looks up {email}",
            },
        }
    ],
}

probes = {
    "messages[0].content (system role)": (email, lambda b: b["messages"][0]["content"]),
    "messages[1].content (user role)": (ssn, lambda b: b["messages"][1]["content"]),
    "metadata.nested.note": (email, lambda b: b["metadata"]["nested"]["note"]),
    "an_unrecognised_key": (ssn, lambda b: b["an_unrecognised_key"]),
    "tools[0].function.description": (email, lambda b: b["tools"][0]["function"]["description"]),
    "tools[0].function.name": ("lookup_customer", lambda b: b["tools"][0]["function"]["name"]),
}

case = {
    "entity": "EMAIL",
    "encoding": "plain",
    "fragmentation": "single_chunk",
    "carrier": "sse-delta-content",
    "request_site": "chat-content",
}


def exchange(body: dict) -> tuple[dict | None, str]:
    state = UpstreamState(segments=segments, case=case)
    server, _url = _serve(_make_upstream(state), port=args.upstream_port)
    headers = {"Content-Type": "application/json"}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
    try:
        with urlopen(  # noqa: S310
            Request(args.url, data=json.dumps(body).encode(), headers=headers), timeout=180
        ) as response:
            sse = response.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        sse = f"<request failed: {exc!r}>"
    finally:
        _stop(server)
    upstream = json.loads(state.received_bodies[0]) if state.received_bodies else None
    text = "".join(
        json.loads(line[6:])["choices"][0]["delta"].get("content", "")
        for line in sse.splitlines()
        if line.startswith("data: ") and line[6:] != "[DONE]"
    )
    return upstream, text


def body_with(front: str, other: str) -> dict:
    """`front` sits in messages[0].content, the slot the capture echoes."""
    return {
        "model": "capture",
        "stream": True,
        "messages": [
            {"role": "system", "content": f"Operator contact is {front}"},
            {"role": "user", "content": f"Please review: {other}"},
        ],
        "metadata": {"nested": {"note": f"ticket raised by {other}"}},
        "an_unrecognised_key": f"jsonrpc-style field carrying {other}",
        "tools": [
            {
                "type": "function",
                "function": {"name": "lookup_customer", "description": f"looks up {other}"},
            }
        ],
    }


# Masking is read off one exchange; every field carries a needle in it.
upstream, _text = exchange(body_with(email, ssn))
if upstream is None:
    print("upstream received nothing; the gateway rejected the request")
    raise SystemExit(1)

probes = {
    "messages[0].content": (email, lambda b: b["messages"][0]["content"]),
    "messages[1].content": (ssn, lambda b: b["messages"][1]["content"]),
    "metadata.nested.note": (ssn, lambda b: b["metadata"]["nested"]["note"]),
    "an_unrecognised_key": (ssn, lambda b: b["an_unrecognised_key"]),
    "tools[0].function.description": (ssn, lambda b: b["tools"][0]["function"]["description"]),
    "tools[0].function.name": ("lookup_customer", lambda b: b["tools"][0]["function"]["name"]),
}

print("REQUEST PATH -- was the value masked before it reached the upstream?")
print(f"  {'field':34} {'masked':>8}")
for label, (needle, getter) in probes.items():
    try:
        sent = getter(upstream)
    except (KeyError, IndexError, TypeError):
        print(f"  {label:34} {'DROPPED':>8}")
        continue
    print(f"  {label:34} {str(needle not in sent):>8}")
print("  (tools[0].function.name is a structural key. Masking it would rename the")
print("   function the provider is asked to call, so False is the correct answer.)")

print()
print("RESPONSE PATH -- each value put in messages[0].content, the echoed slot:")
print(f"  {'value':34} {'masked out':>11} {'restored':>9}")
for name, value, other in (("EMAIL", email, ssn), ("SSN", ssn, email)):
    up, text = exchange(body_with(value, other))
    sent = up["messages"][0]["content"] if up else ""
    print(f"  {name:34} {str(value not in sent):>11} {str(value in text):>9}")

print()
print("prompt the upstream received (masking exchange):")
print(json.dumps(upstream, indent=1)[:800])
