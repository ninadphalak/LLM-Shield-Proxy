"""A minimal gateway driving Guardrails AI's own streaming accumulator.

WHAT IS BEING MEASURED, AND WHAT IS NOT. Guardrails AI 0.10.2 has no restore half: there is
no `reidentify`, `deanonymize` or `unredact` anywhere in its 178 modules. **It does not
claim rehydration and it is not scored for failing to do it.** This row exists for the one
thing it does have that nothing else inspected does:

    Validator.validate_stream(chunk, metadata)

which does not scan a chunk in isolation. It ACCUMULATES:

    accumulated_chunks.append(chunk)
    accumulated_text = "".join(accumulated_chunks)
    split_contents = self._chunking_function(accumulated_text)

and its own docstring says "If the LLM chunk is smaller than the validator's chunking
strategy, it will be accumulated until it reaches the desired size. In the meantime, the
validator will return None." That is retention across chunk boundaries, shipping, in OSS,
from a project that is not this one. The default `_chunking_function` is
`split_sentence_word_tokenizers_jl_separator` -- it holds to the next **sentence** boundary.

**So the detector is held constant and only the accumulation strategy varies.** The four
patterns below are the profile's own `_DETECTORS`, so this row is directly comparable with
`chunk-local` (no accumulation) and `bounded-retention` (a bounded `L = N-1` suffix carry).
Three strategies, one detector, one corpus. `tests/conformance/test_guardrails_detectors_in_sync.py`
fails if these patterns drift from the harness's.

**The request path is deliberately not masked**, because there is nothing to restore it
with, and the run must be recorded with `V2_REQUEST_PATH_REDACTION=not-configured` so the
report says so.

**MEASURED FidelityRate: 0.00, and this paragraph predicted 1.00.** The prediction was
that the absence of a transformation would score vacuously well, the way Portkey's does.
It does not, and the reason is the point of the row: the unmasked echo comes back, the
validator has no way to know it is the caller's own data, and it redacts it. Restoring is
not the absence of redacting. The prediction was written before the run and left standing
after it -- which is the same class of error as everything else this profile records, one
level down, so it is corrected here rather than deleted.

RUNNING IT. Guardrails AI pulls `litellm`, `openai` and `langchain-core`, so it does not go
in the harness environment either -- and note it would install the very gateway that is
already under test in another row.

    py -3.12 -m venv venv-guardrails
    venv-guardrails/Scripts/pip install guardrails-ai==0.10.2
    venv-guardrails/Scripts/python benchmarks/guardrails-v2-profile/gateway.py --port 8791
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict
from urllib.request import Request, urlopen

from guardrails.classes.validation.validation_result import FailResult, PassResult
from guardrails.validator_base import Validator, register_validator

# VERBATIM from `pii_leak_benchmark.v2_emitter._DETECTORS`. Copied rather than imported
# because the harness must not be installed into the environment of software under test,
# and a conformance test asserts the two lists have not drifted.
DETECTORS = (
    ("EMAIL", re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.I)),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("CARDPAN", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("USPHONE", re.compile(r"\b\d{3}-\d{3}-\d{4}\b")),
)

UPSTREAM = os.environ.get("GUARDRAILS_UPSTREAM", "http://127.0.0.1:8799/v1/chat/completions")


@register_validator(name="v2-profile/pii-regex", data_type="string")
class ProfilePii(Validator):
    """The profile's detector set, inside Guardrails' own accumulator.

    `validate` is deliberately trivial. Everything interesting in this row happens in the
    inherited `validate_stream`, which decides WHEN this method gets to see text.
    """

    def validate(self, value: Any, metadata: Dict[str, Any]) -> Any:
        text = value if isinstance(value, str) else str(value)
        redacted = text
        for _entity, pattern in DETECTORS:
            redacted = pattern.sub("[REDACTED]", redacted)
        if redacted != text:
            return FailResult(error_message="PII detected", fix_value=redacted)
        return PassResult()


def _emit(result) -> str:
    """What the client is allowed to see for one accumulated unit."""
    if result is None:
        return ""  # still accumulating; the validator is holding the text back
    fix = getattr(result, "fix_value", None)
    if fix is not None:
        return fix
    return getattr(result, "validated_chunk", "") or ""


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
            # The request is forwarded UNCHANGED. See the module docstring: there is no
            # restore half, so masking here would guarantee a fidelity failure that says
            # nothing about the product.
            request = Request(
                UPSTREAM, data=raw, headers={"Content-Type": "application/json"}
            )
            with urlopen(request, timeout=120) as response:  # noqa: S310
                upstream_sse = response.read().decode("utf-8", "replace")

            deltas = []
            for line in upstream_sse.splitlines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    continue
                deltas.append(json.loads(data)["choices"][0]["delta"])

            content_validator = ProfilePii(on_fail="fix")
            sibling_validator = ProfilePii(on_fail="fix")

            out_events = []
            for delta in deltas:
                event = {}
                text = delta.get("content", "")
                if text:
                    event["content"] = _emit(content_validator.validate_stream(text, {}))
                else:
                    event["content"] = ""
                for key, value in delta.items():
                    if key != "content" and isinstance(value, str) and value:
                        event[key] = _emit(sibling_validator.validate_stream(value, {}))
                out_events.append(event)

            # Flush whatever the accumulator is still holding at end of stream. Without
            # this the tail of every response would be silently dropped, which would look
            # like perfect redaction and be nothing of the sort.
            for validator, key in ((content_validator, "content"), (sibling_validator, "record_field")):
                tail = _emit(validator.validate_stream("", {}, remainder=True))
                if tail:
                    out_events.append({"content": tail} if key == "content" else {"content": "", key: tail})

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
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--upstream", default=UPSTREAM)
    args = parser.parse_args()
    UPSTREAM = args.upstream

    server = ThreadingHTTPServer(("127.0.0.1", args.port), _handler_class())
    print(f"guardrails gateway on :{args.port} -> {UPSTREAM}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
