"""A minimal OpenAI-compatible gateway built from LLM Guard's own scanners.

WHY THIS EXISTS. LLM Guard (`protectai/llm-guard`) is the only product this survey found
that ships every piece the v2 response split asks for:

    input_scanners.Anonymize     mask the request, record the mapping in a Vault
    output_scanners.Sensitive    redact PII in the model's reply
    output_scanners.Deanonymize  restore the caller's own values from the Vault

That is exactly the pair of opposite behaviours the profile measures, from one vendor. It
is not a gateway, so this file is the thinnest possible gateway around it: nothing here
detects or masks anything, and every decision about what is PII and what to do with it
belongs to LLM Guard.

THE ONE CHOICE THIS FILE MAKES, and it is the whole experiment. `Sensitive.scan` and
`Deanonymize.scan` take the COMPLETE model output. A streaming integrator therefore has to
pick one of two things, and neither is obviously wrong:

    LLMGUARD_MODE=chunk-local   call the scanners on each SSE delta as it arrives.
                                Incremental delivery is preserved. A value split across
                                two deltas is never whole in front of the scanner.
    LLMGUARD_MODE=buffered      accumulate the whole response, scan once, emit it as a
                                single chunk. The scanner always sees whole values, and
                                incremental delivery is gone.

Both are faithful uses of the published API. The profile measures what each costs.

RUNNING IT. LLM Guard pins `torch>=2.4.0`, `transformers==4.51.3` and Presidio, and
declares `requires_python: <3.13,>=3.10`, so it does NOT install alongside the harness
(CPython 3.14) and it is not a light dependency. Use a separate 3.10-3.12 environment:

    py -3.12 -m venv venv-llmguard
    venv-llmguard/Scripts/pip install llm-guard==0.3.16
    venv-llmguard/Scripts/python benchmarks/llm-guard-v2-profile/gateway.py \
        --port 8790 --upstream http://127.0.0.1:8799/v1/chat/completions

Software under test does not belong in the harness environment; see
`../litellm-v2-profile/config.docker.yaml` for the same rule stated for LiteLLM.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

from llm_guard.input_scanners import Anonymize
from llm_guard.output_scanners import Deanonymize, Sensitive
from llm_guard.vault import Vault

# Values in these keys decide what a request MEANS rather than what it discloses. The
# reference gateway leaves them alone for the same reason; masking `model` would change
# which deployment is called.
STRUCTURAL_KEYS = frozenset({"model", "name", "role", "type"})

MODE = os.environ.get("LLMGUARD_MODE", "chunk-local")
UPSTREAM = os.environ.get("LLMGUARD_UPSTREAM", "http://127.0.0.1:8799/v1/chat/completions")

# BUILT ONCE. The first version of this file constructed Anonymize, Sensitive and
# Deanonymize per request. Each one builds a Presidio analyzer and loads its recognizers,
# so a sweep died after roughly a hundred requests -- the harness reported all 32 cases
# inconclusive for that seed, which is the "target answered nothing" guard doing its job
# and refusing to record an all-zeros row as a clean one.
#
# That was this wrapper's bug, not LLM Guard's, and it must not be reported as a property
# of the product. Scanners are stateless with respect to a request; the only per-request
# state is the Vault, which is cleared under a lock below.
_VAULT = Vault()
_ANONYMIZE = Anonymize(_VAULT)
# redact=True is NOT the default. `Sensitive(redact: bool = False)` detects and flags -- it
# returns the output UNCHANGED with is_valid=False and leaves the decision to the caller.
# Measured before this line was written: the injected address was found ("Found sensitive
# data in the output") and still reached the client verbatim. A row run on the default would
# have scored LeakRate 1.00 and been a fact about this file rather than about LLM Guard.
_SENSITIVE = Sensitive(redact=True)
_DEANONYMIZE = Deanonymize(_VAULT)
# One request at a time. The scanners and the vault are shared, and two concurrent requests
# would restore each other's values -- a cross-request leak invented by the wrapper.
_LOCK = threading.Lock()


def _mask_body(payload, scanner: Anonymize):
    """Walk the WHOLE request body, not just the chat shapes.

    A gateway that masked only `messages[*].content` would score a perfect FidelityRate on
    the chat-content site and egress every other one. That is what the `request_site` axis
    exists to expose, so this walker does not get to be the thing that hides it.
    """

    def walk(node, key=None):
        if isinstance(node, dict):
            return {k: walk(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, key) for v in node]
        if isinstance(node, str) and key not in STRUCTURAL_KEYS and node.strip():
            sanitized, _valid, _score = scanner.scan(node)
            return sanitized
        return node

    return walk(payload)


def _apply_output(text: str, prompt: str, sensitive: Sensitive, deanon: Deanonymize) -> str:
    """Redact first, restore second. The order is load-bearing.

    `Deanonymize` turns placeholders back into the caller's real values. Running it before
    `Sensitive` would hand those restored values straight to the redactor, which would then
    remove them again -- destroying the fidelity half to satisfy the leak half. Redacting
    first leaves the placeholders alone (they are not PII) and lets the restore run last.
    """
    if not text:
        return text
    redacted, _valid, _score = sensitive.scan(prompt, text)
    restored, _valid, _score = deanon.scan(prompt, redacted)
    return restored


def _handler_class():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # noqa: A003
            return

        def do_POST(self):  # noqa: N802
            try:
                self._respond()
            except Exception as exc:  # noqa: BLE001
                body = json.dumps({"gateway_error": f"{type(exc).__name__}: {exc}"}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

        def _respond(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            payload = json.loads(raw.decode("utf-8", "replace"))

            with _LOCK:
                return self._scan(payload)

        def _scan(self, payload):
            # Empty the shared vault. It is the mapping between the two halves, so carrying
            # one request's entries into the next would restore another caller's values.
            _VAULT.get().clear()
            anonymize, sensitive, deanonymize = _ANONYMIZE, _SENSITIVE, _DEANONYMIZE

            masked = _mask_body(payload, anonymize)
            prompt_text = json.dumps(masked)

            request = Request(
                UPSTREAM,
                data=json.dumps(masked).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=120) as response:  # noqa: S310
                upstream_sse = response.read().decode("utf-8", "replace")

            events = []
            for line in upstream_sse.splitlines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    continue
                events.append(json.loads(data)["choices"][0]["delta"])

            out_events = []
            if MODE == "buffered":
                # Accumulate everything, scan once, emit one chunk. The scanners see whole
                # values; the client sees the whole reply at the end and nothing before it.
                joined = "".join(e.get("content", "") for e in events)
                siblings = "".join(
                    v for e in events for k, v in e.items()
                    if k != "content" and isinstance(v, str)
                )
                out_events.append({"content": _apply_output(joined, prompt_text, sensitive, deanonymize)})
                if siblings:
                    out_events.append(
                        {"content": "", "record_field":
                         _apply_output(siblings, prompt_text, sensitive, deanonymize)}
                    )
            else:
                for delta in events:
                    event = {"content": _apply_output(
                        delta.get("content", ""), prompt_text, sensitive, deanonymize)}
                    for key, value in delta.items():
                        if key != "content" and isinstance(value, str):
                            event[key] = _apply_output(value, prompt_text, sensitive, deanonymize)
                    out_events.append(event)

            body = b""
            for event in out_events:
                delta = {"content": event.get("content", "")}
                for key, value in event.items():
                    if key != "content":
                        delta[key] = value
                body += b"data: " + json.dumps({"choices": [{"delta": delta}]}).encode() + b"\n\n"
            body += b"data: [DONE]\n\n"

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

    return Handler


def main() -> int:
    global UPSTREAM  # noqa: PLW0603
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--upstream", default=UPSTREAM)
    args = parser.parse_args()
    UPSTREAM = args.upstream

    server = ThreadingHTTPServer(("127.0.0.1", args.port), _handler_class())
    print(f"llm-guard gateway mode={MODE} on :{args.port} -> {UPSTREAM}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
