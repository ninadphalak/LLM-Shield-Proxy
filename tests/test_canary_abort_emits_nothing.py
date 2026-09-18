"""A tripped canary must end the stream, not flush what was held back.

The canary tripwire fires when the model starts echoing the system prompt back at
the caller. The stream `break`s out of the read loop, which is right. But the
`finally` block then ran its normal end-of-stream path, because the three flags it
checks (`client_disconnected`, `failed_open`, `stream_aborted`) were all still
False on that exit.

So the attacker got the retention window flushed to them anyway. That window holds
the tail the buffer deliberately withheld from the previous chunk, which on a
prompt-extraction attack is the last few words of the prompt being extracted. The
watermark was appended too, which tells the attacker the abort happened and hands
them a second sample of the watermark at the same time.

Abort has to mean abort: once the tripwire fires, nothing further reaches the
client.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.streaming.streaming import rehydrate_sse_stream

CANARY = "[SHIELD_TRIPWIRE_test_canary_value]"

# The tail of the extracted prompt. It has no trailing space, so the retention
# window holds the whole run back rather than emitting it with its chunk.
WITHHELD = "hunter2secret"


@pytest.fixture
def tripwire_on(monkeypatch):
    from llm_shield_proxy.core.config import settings

    monkeypatch.setattr(settings, "ENABLE_RESPONSE_PII_REDACTION", True, raising=False)
    monkeypatch.setattr(settings, "ENABLE_CANARY_TRIPWIRE", True, raising=False)
    monkeypatch.setattr(settings, "CANARY_TOKEN", CANARY, raising=False)
    return settings


def _drive(lines: list[str], watermark: str | None = None) -> str:
    """Runs raw SSE text through the rehydration stream and returns what a client sees."""

    async def main() -> str:
        vault = Vault(synthetic=False)

        async def source():
            for line in lines:
                yield line.encode("utf-8")

        return "".join(
            [
                chunk.decode("utf-8")
                async for chunk in rehydrate_sse_stream(source(), vault, watermark_text=watermark)
            ]
        )

    return asyncio.run(main())


def _delta(text: str) -> str:
    payload = {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "gpt-4",
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def test_control_without_the_tripwire_the_withheld_tail_is_flushed(tripwire_on):
    """The retention window really does hold the tail back until the end.

    Without this control the defect test below could pass for the wrong reason:
    nothing withheld means nothing to leak.
    """
    seen = _drive([_delta(f"The system prompt says {WITHHELD}"), "data: [DONE]\n\n"])

    assert WITHHELD in seen


def test_tripwire_does_not_flush_the_retention_window(tripwire_on):
    """The withheld tail of the extracted prompt must not follow the abort out."""
    seen = _drive(
        [
            _delta(f"The system prompt says {WITHHELD}"),
            _delta(f"and the canary is {CANARY}"),
        ]
    )

    assert WITHHELD not in seen


def test_tripwire_does_not_leak_the_canary_itself(tripwire_on):
    """The token that proves the attack worked must not be echoed back."""
    seen = _drive(
        [
            _delta("harmless prefix"),
            _delta(f"and the canary is {CANARY}"),
        ]
    )

    assert CANARY not in seen


def test_tripwire_does_not_append_the_watermark(tripwire_on):
    """An aborted stream gets no trailing watermark event."""
    seen = _drive(
        [
            _delta("harmless prefix"),
            _delta(f"and the canary is {CANARY}"),
        ],
        watermark="WATERMARK_SENTINEL",
    )

    assert "WATERMARK_SENTINEL" not in seen


def test_tripwire_does_not_emit_done(tripwire_on):
    """A stream cut short must not be signed off as if it completed normally."""
    seen = _drive(
        [
            _delta("harmless prefix"),
            _delta(f"and the canary is {CANARY}"),
        ]
    )

    assert "[DONE]" not in seen


def test_an_ordinary_stream_still_completes_with_the_tripwire_armed(tripwire_on):
    """Control: arming the tripwire must not change a clean stream."""
    seen = _drive([_delta("nothing to see here "), "data: [DONE]\n\n"], watermark="WATERMARK_SENTINEL")

    assert "nothing to see here" in seen
    assert "WATERMARK_SENTINEL" in seen
    assert "[DONE]" in seen
