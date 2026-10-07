"""What deep payload redaction costs, on your own traffic shapes.

Question under test
-------------------
`redact_payload` walks every field a request carries, because a passthrough proxy
forwards every field and anything left unwalked is egressed. Walking costs time.
This measures how much, and shows where the cost actually comes from.

The answer is not "depth is expensive". Depth is cheap. Blobs are expensive: one
base64 image is larger than the rest of a payload combined, and scanning it finds
nothing, because a text detector cannot match an image. That is why
`PAYLOAD_MAX_REDACT_STRING_LENGTH` exists, and why raising it is the one setting
here that will visibly slow the proxy.

Reading the output
------------------
`walked` is the current behaviour. `no ceiling` is the same walk with the blob
guard removed, i.e. what happens if `PAYLOAD_MAX_REDACT_STRING_LENGTH` is raised
past your largest attachment. The gap between those two columns is the cost of
inspecting blobs.

These are local microbenchmarks. They exclude network, TLS and model time, which
dominate a real request by orders of magnitude. Use them to compare settings
against each other, never as an end-to-end latency claim.

Usage
-----
    python benchmarks/payload_walk_latency.py
    python benchmarks/payload_walk_latency.py --turns 200 --image-mb 4
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from typing import Any, Callable

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.pii_engine import pii_engine
from llm_shield_proxy.engines.vault import Vault

SAMPLE_EMAIL = "jane.doe@example.com"


def chat_payload(turns: int) -> dict[str, Any]:
    """A multi-turn chat with the metadata and tools a real deployment sends."""
    return {
        "model": "gpt-4o",
        "user": SAMPLE_EMAIL,
        "metadata": {"tenant": "acme", "requested_by": SAMPLE_EMAIL},
        "messages": [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"Turn {index}: chasing the invoice for {SAMPLE_EMAIL}.",
            }
            for index in range(turns)
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "send_mail",
                    "description": f"Send an invoice, for example to {SAMPLE_EMAIL}.",
                    "parameters": {"type": "object", "properties": {"to": {"type": "string"}}},
                },
            }
        ],
    }


def message_image_payload(megabytes: float) -> dict[str, Any]:
    """An inline image in `messages`, which the shape-aware walk already owns.

    The blob ceiling does nothing here: `messages` is walked by shape, and the deep
    walk never descends into it. Included so the contrast with the row below is
    visible rather than asserted.
    """
    blob = base64.b64encode(b"x" * int(megabytes * 1024 * 1024)).decode()
    return {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Whose invoice is this, {SAMPLE_EMAIL}?"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{blob}"}},
                ],
            }
        ],
    }


def attached_blob_payload(megabytes: float) -> dict[str, Any]:
    """A blob in a field this gateway does not know by name.

    This is what the ceiling is for. A provider-specific attachment, a document
    pushed through `metadata`, anything a future API adds: the deep walk reaches it,
    and without the ceiling it would be scanned end to end for no possible match.
    """
    blob = base64.b64encode(b"x" * int(megabytes * 1024 * 1024)).decode()
    return {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": f"Invoice for {SAMPLE_EMAIL}"}],
        "attachments": [{"filename": "scan.png", "data": blob}],
    }


def _time(run: Callable[[], Any], repeats: int) -> float:
    """Milliseconds per call, discarding one warm-up run."""
    run()
    started = time.perf_counter()
    for _ in range(repeats):
        run()
    return (time.perf_counter() - started) / repeats * 1000


def measure(label: str, payload: dict[str, Any], repeats: int) -> None:
    size_kb = len(json.dumps(payload)) / 1024
    ceiling = settings.PAYLOAD_MAX_REDACT_STRING_LENGTH
    deep_enabled = settings.ENABLE_DEEP_PAYLOAD_REDACTION

    try:
        settings.ENABLE_DEEP_PAYLOAD_REDACTION = False
        shapes_only = _time(lambda: pii_engine.redact_payload(payload, Vault()), repeats)

        settings.ENABLE_DEEP_PAYLOAD_REDACTION = True
        walked = _time(lambda: pii_engine.redact_payload(payload, Vault()), repeats)

        settings.PAYLOAD_MAX_REDACT_STRING_LENGTH = 1_000_000_000
        no_ceiling = _time(lambda: pii_engine.redact_payload(payload, Vault()), repeats)
    finally:
        settings.PAYLOAD_MAX_REDACT_STRING_LENGTH = ceiling
        settings.ENABLE_DEEP_PAYLOAD_REDACTION = deep_enabled

    print(
        f"{label:24s} {size_kb:9.1f} {shapes_only:14.3f} {walked:11.3f} "
        f"{no_ceiling:13.3f} {no_ceiling / walked if walked else 0:8.1f}x"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turns", type=int, default=100, help="Longest chat to measure")
    parser.add_argument("--image-mb", type=float, default=1.0, help="Largest inline image to measure")
    args = parser.parse_args()

    print(f"blob ceiling in force: PAYLOAD_MAX_REDACT_STRING_LENGTH={settings.PAYLOAD_MAX_REDACT_STRING_LENGTH}")
    print()
    print(f"{'payload':24s} {'size KB':>9s} {'shapes only ms':>14s} {'walked ms':>11s} {'no ceiling ms':>13s} {'blob cost':>9s}")
    measure("chat, 4 turns", chat_payload(4), 40)
    measure("chat, 20 turns", chat_payload(20), 20)
    measure(f"chat, {args.turns} turns", chat_payload(args.turns), 10)
    measure("image in messages 1 MB", message_image_payload(1.0), 5)
    measure("blob in unknown field 100 KB", attached_blob_payload(0.1), 10)
    measure(f"blob in unknown field {args.image_mb:g} MB", attached_blob_payload(args.image_mb), 5)
    print()
    print("shapes only  = ENABLE_DEEP_PAYLOAD_REDACTION=false, the fields this gateway knows by name")
    print("walked       = current behaviour, every field walked, blobs skipped")
    print("no ceiling   = the same walk with the blob guard removed")
    print("blob cost    = what raising PAYLOAD_MAX_REDACT_STRING_LENGTH past your attachments would cost")


if __name__ == "__main__":
    main()
