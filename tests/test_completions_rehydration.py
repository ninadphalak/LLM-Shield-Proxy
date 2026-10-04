"""Legacy /v1/completions replies are rehydrated, and rehydration never edits its input.

A completions choice carries its text in `choice.text`, not in `message` or `delta`. The
reply walks keyed on those two, so the caller got the placeholder back instead of the value
they sent, streaming or not.

Rehydration also has to leave the upstream reply as it came: placeholders are numbered per
session, so two callers' redacted replies can be identical, and anything that keeps a reply
after rehydration (a cache, a log, a trace) would then hold one caller's plaintext under a
key another caller can hit. The proxy keeps no LLM reply cache; these tests pin that the
rehydration step works on a copy, so adding one later cannot store plaintext by accident.
"""

from __future__ import annotations

import asyncio
import copy
import json

import pytest

from llm_shield_proxy.api.main import _rehydrate_json_response
from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.streaming.streaming import rehydrate_sse_stream

EMAIL = "alice@example.com"


@pytest.fixture
def vault():
    vault = Vault(synthetic=False)
    assert vault.get_or_create_token(EMAIL, "EMAIL_ADDRESS") == "[EMAIL_ADDRESS_1]"
    return vault


def _completion(text: str, finish_reason="stop") -> dict:
    return {
        "id": "cmpl-1",
        "object": "text_completion",
        "model": "gpt-3.5-turbo-instruct",
        "choices": [{"index": 0, "text": text, "logprobs": None, "finish_reason": finish_reason}],
    }


def _drive(vault, events: list[dict], done: bool = True) -> list[dict]:
    """Runs SSE events through the stream and returns the JSON events a client sees."""

    async def main() -> str:
        async def source():
            for event in events:
                yield f"data: {json.dumps(event)}\n\n".encode()
            if done:
                yield b"data: [DONE]\n\n"

        return "".join([c.decode() async for c in rehydrate_sse_stream(source(), vault, path="v1/completions")])

    out = asyncio.run(main())
    return [
        json.loads(line[5:].strip())
        for line in out.splitlines()
        if line.startswith("data:") and line[5:].strip() != "[DONE]"
    ]


def _joined_text(events: list[dict]) -> str:
    return "".join(
        choice.get("text", "") for event in events for choice in event.get("choices", []) if isinstance(choice, dict)
    )


def test_non_streaming_completion_text_is_rehydrated(vault):
    restored = _rehydrate_json_response(_completion("Mail [EMAIL_ADDRESS_1] now"), vault)
    assert restored["choices"][0]["text"] == f"Mail {EMAIL} now"


def test_streaming_completion_text_is_rehydrated(vault):
    events = _drive(
        vault,
        [
            _completion("Mail [EMAIL_AD", finish_reason=None),
            _completion("DRESS_1] now", finish_reason=None),
            _completion("", finish_reason="stop"),
        ],
    )
    assert _joined_text(events) == f"Mail {EMAIL} now"
    assert "[EMAIL_ADDRESS_1]" not in json.dumps(events)
    # Held-back text leaves in the completions shape, never as a chat `delta`.
    assert all("delta" not in choice for event in events for choice in event["choices"])


@pytest.mark.parametrize("done", [True, False], ids=["done", "connection-close"])
def test_text_held_back_at_end_of_stream_is_emitted(vault, done):
    """No finish_reason ever arrives; the tail still reaches the caller, restored."""
    events = _drive(vault, [_completion("Mail [EMAIL_ADDRESS_1]", finish_reason=None)], done=done)
    assert _joined_text(events) == f"Mail {EMAIL}"


def test_a_finishing_event_carries_the_held_back_text(vault):
    """The tail goes into the event that finishes the choice, before `[DONE]`."""
    events = _drive(vault, [_completion("Mail [EMAIL_ADDRESS_1]", finish_reason="stop")])
    finishing = [e for e in events if e["choices"][0].get("finish_reason") == "stop"]
    assert finishing and finishing[-1]["choices"][0]["text"].endswith(EMAIL)


@pytest.mark.parametrize(
    "reply",
    [
        _completion("Mail [EMAIL_ADDRESS_1]"),
        {"choices": [{"index": 0, "message": {"role": "assistant", "content": "Mail [EMAIL_ADDRESS_1]"}}]},
        {"content": [{"type": "text", "text": "Mail [EMAIL_ADDRESS_1]"}], "role": "assistant"},
    ],
    ids=["completions", "chat", "anthropic"],
)
def test_rehydration_leaves_the_upstream_reply_redacted(vault, reply):
    stored = copy.deepcopy(reply)
    restored = _rehydrate_json_response(reply, vault)

    assert EMAIL in json.dumps(restored)
    assert reply == stored
    assert EMAIL not in json.dumps(reply)
