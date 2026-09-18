"""A deliberate safety limit must not be forgiven by FAIL_OPEN.

Greptile P1 on #42, and the finding reproduces against `MAX_STREAM_WINDOWS`, which is
pre-existing and untouched by that PR. So this is a live bypass, not a new one.

The chunk-level handler treats any exception as "rehydration failed". Under FAIL_OPEN it
then sets `failed_open` and forwards every later chunk raw. The retention-window cap, the
Anthropic tool-ordinal cap, the output ceiling and the line-accumulator ceiling all bound
work an UPSTREAM controls, so an upstream could trip one on purpose and switch scanning
off for the rest of the stream.

Measured before the fix: 257 distinct choice indices, then `victim@example.com`, and the
address reached the client in clear.

FAIL_OPEN exists to keep a stream alive through an unexpected bug in this proxy. It does
not exist to honour an upstream's request to stop inspecting it.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.streaming import streaming as streaming_module
from llm_shield_proxy.streaming.streaming import rehydrate_sse_stream

SECRET = "victim@example.com"


@pytest.fixture
def fail_open(monkeypatch):
    monkeypatch.setattr(
        streaming_module.settings, "ENABLE_RESPONSE_PII_REDACTION", True, raising=False
    )
    monkeypatch.setattr(streaming_module.settings, "SHIELD_FAILURE_MODE", "FAIL_OPEN")
    return streaming_module.settings


def _delta(index: int, text: str) -> str:
    payload = {"choices": [{"index": index, "delta": {"content": text}, "finish_reason": None}]}
    return f"data: {json.dumps(payload)}\n\n"


def _drive(lines: list[str]) -> str:
    async def main() -> str:
        async def source():
            for line in lines:
                yield line.encode("utf-8")

        return "".join(
            [c.decode("utf-8") async for c in rehydrate_sse_stream(source(), Vault(synthetic=False))]
        )

    return asyncio.run(main())


def test_window_cap_breach_does_not_disable_scanning(fail_open, monkeypatch):
    """The pre-existing cap. An upstream must not be able to buy raw passthrough."""
    monkeypatch.setattr(streaming_module, "MAX_STREAM_WINDOWS", 2)

    lines = [_delta(i, f"part{i} ") for i in range(4)]
    lines.append(_delta(0, f"here is {SECRET} "))
    lines.append("data: [DONE]\n\n")

    out = _drive(lines)

    assert SECRET not in out, "tripping the window cap turned redaction off"
    assert "shield_stream_aborted" in out, "the abort was not announced to the client"


def test_ordinal_cap_breach_does_not_disable_scanning(fail_open, monkeypatch):
    """The cap this PR adds, held to the same rule."""
    monkeypatch.setattr(streaming_module, "MAX_ANTHROPIC_TOOL_ORDINALS", 1)

    lines: list[str] = []
    for index in range(3):
        start = {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "tool_use", "id": f"toolu_{index}", "name": "f"},
        }
        lines.append(f"data: {json.dumps(start)}\n\n")
    lines.append(_delta(0, f"here is {SECRET} "))
    lines.append("data: [DONE]\n\n")

    out = _drive(lines)

    assert SECRET not in out
    assert "shield_stream_aborted" in out


def test_an_ordinary_failure_still_fails_open(fail_open, monkeypatch):
    """Control. FAIL_OPEN must still do what it is for.

    An unexpected bug inside this proxy, as opposed to a limit an upstream chose to
    trip, still keeps the stream alive. Otherwise this change would quietly convert
    every fail-open deployment into a fail-closed one.
    """
    def _boom(*_args, **_kwargs):
        raise RuntimeError("unexpected internal failure")

    monkeypatch.setattr(streaming_module, "_redact_sibling_strings", _boom)

    out = _drive([_delta(0, "ordinary content "), "data: [DONE]\n\n"])

    assert "shield_stream_aborted" not in out, "an ordinary error must not abort under FAIL_OPEN"
