"""Failing closed must still end the stream, not leave the client hanging.

The chunk-level handler in `rehydrate_sse_stream` catches anything the per-line
handlers did not, and in FAIL_CLOSED mode it `return`ed. Correct as far as it goes:
nothing unscanned should reach the client. But it returned *silently*. The response
body simply stopped, with no `[DONE]` and no error event, so a client saw a stream
that had gone quiet and could not tell an abort from a stalled network. It waits for
a terminator that is never coming.

The audit filed this as "one malformed line kills the whole stream". That trigger
does not reproduce: every malformed shape probed (`choices: []` on a usage chunk, a
`choices` dict, a string `delta`, a non-numeric `index`, unparseable JSON, 1500-deep
nesting) is already caught per line and forwarded. The reachable trigger is
retention-window exhaustion, which is the cap doing its job. The defect is the
manner of the abort, not the abort.

So: still fail closed, still emit nothing unscanned, but terminate the stream
properly so the client's parser finishes.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.streaming.streaming import MAX_STREAM_WINDOWS, rehydrate_sse_stream

SECRET = "sk-live-abcdefghijklmnopqrstuvwxyz012345"


@pytest.fixture
def fail_closed(monkeypatch):
    from llm_shield_proxy.core.config import settings

    monkeypatch.setattr(settings, "ENABLE_RESPONSE_PII_REDACTION", True, raising=False)
    monkeypatch.setattr(settings, "SHIELD_FAILURE_MODE", "FAIL_CLOSED", raising=False)
    return settings


def _drive(lines: list[str]) -> str:
    async def main() -> str:
        vault = Vault(synthetic=False)

        async def source():
            for line in lines:
                yield line.encode("utf-8")

        return "".join([c.decode("utf-8") async for c in rehydrate_sse_stream(source(), vault)])

    return asyncio.run(main())


def _delta(index: int, text: str) -> str:
    payload = {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "gpt-4",
        "choices": [{"index": index, "delta": {"content": text}, "finish_reason": None}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def _exhaust_windows() -> list[str]:
    """One line per choice index, one past the cap, then more content."""
    lines = [_delta(i, f"part{i} ") for i in range(MAX_STREAM_WINDOWS + 1)]
    lines.append(_delta(0, f"trailing {SECRET} "))
    lines.append("data: [DONE]\n\n")
    return lines


def test_control_the_cap_still_stops_the_stream(fail_closed):
    """Failing closed is the correct behaviour and must not regress into failing open."""
    seen = _drive(_exhaust_windows())

    assert "trailing" not in seen
    assert SECRET not in seen


def test_the_aborted_stream_is_terminated(fail_closed):
    """The client must receive an end-of-stream marker rather than silence."""
    seen = _drive(_exhaust_windows())

    assert seen.rstrip().endswith("data: [DONE]")


def test_the_abort_is_announced_as_an_error(fail_closed):
    """A terminator alone would look like a short but successful reply."""
    seen = _drive(_exhaust_windows())

    events = [
        json.loads(line[len("data:") :].strip())
        for line in seen.splitlines()
        if line.startswith("data:") and line[len("data:") :].strip() != "[DONE]"
    ]
    errors = [e for e in events if isinstance(e, dict) and "error" in e]

    assert errors, "aborted stream carried no error event"
    assert errors[-1]["error"]["type"] == "shield_stream_aborted"


def test_the_error_event_does_not_quote_the_exception(fail_closed):
    """Invariant 4. An exception message can carry the payload that caused it.

    The window-cap ValueError names the cap, which is harmless, but the handler is
    generic and the next exception through it may not be. The client-facing event
    carries a fixed string, never `str(exc)`.
    """
    seen = _drive(_exhaust_windows())

    assert "retention windows" not in seen
    assert "ValueError" not in seen


def test_the_log_records_the_type_not_the_message(fail_closed, caplog):
    """Same invariant on the log sink: type names only, never the rendered exception."""
    import logging

    with caplog.at_level(logging.ERROR):
        _drive(_exhaust_windows())

    aborts = [r for r in caplog.records if "FAIL_CLOSED" in r.getMessage()]
    assert aborts, "abort was not logged"
    message = aborts[-1].getMessage()
    assert "ValueError" in message
    assert "retention windows" not in message


def test_a_clean_stream_carries_no_error_event(fail_closed):
    """Control: the terminator must not be manufactured on the happy path."""
    seen = _drive([_delta(0, "all fine here "), "data: [DONE]\n\n"])

    assert "shield_stream_aborted" not in seen
    assert seen.count("[DONE]") == 1
