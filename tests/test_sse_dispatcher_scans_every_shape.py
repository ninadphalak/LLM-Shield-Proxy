"""Every message shape reaches the scanner, not only the ones the dispatcher knows.

The SSE dispatcher recognised a handful of event shapes and routed those into the
retention buffer and the model-originated scan. Anything else fell through the branch
chain and was forwarded to the client byte for byte, never scanned at all.

The scanning engine itself was fuzzed hard and is sound. The defect was that whole
classes of ordinary message never reached it:

- a reasoning model's `reasoning_content`, which is how o1-style models talk
- content sent as a list of parts, which is the modern OpenAI shape
- `data:` written without the optional space, which the SSE spec allows
- the last line of a truncated stream, which happens whenever a connection drops
- Anthropic's `message_start`
- legacy completions that carry `text` instead of `delta`

Each test below fails on the unfixed dispatcher.
"""

from __future__ import annotations

import asyncio

import pytest

from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.streaming.streaming import rehydrate_sse_stream

# PII the model invented. It is not in the vault, so it must be redacted, not restored.
MODEL_PII = "nuwpcbba@example.com"


@pytest.fixture
def response_redaction_on(monkeypatch):
    from llm_shield_proxy.core.config import settings

    monkeypatch.setattr(settings, "ENABLE_RESPONSE_PII_REDACTION", True, raising=False)
    return settings


def _drive(lines: list[str]) -> str:
    """Runs raw SSE text through the rehydration stream and returns what a client sees."""

    async def main() -> str:
        vault = Vault(synthetic=False)

        async def source():
            for line in lines:
                yield line.encode("utf-8")

        return "".join([c.decode("utf-8") async for c in rehydrate_sse_stream(source(), vault)])

    return asyncio.run(main())


def _event(payload: str) -> str:
    return f"data: {payload}\n\n"


def test_control_an_ordinary_delta_is_scanned(response_redaction_on):
    """If this fails, the harness is wrong rather than the dispatcher."""
    out = _drive([_event('{"choices":[{"index":0,"delta":{"content":"call %s"}}]}' % MODEL_PII)])
    assert MODEL_PII not in out


def test_reasoning_content_only_delta_is_scanned(response_redaction_on):
    """o1-style models stream their reasoning in a delta with no `content` key."""
    out = _drive(
        [_event('{"choices":[{"index":0,"delta":{"reasoning_content":"thinking %s"}}]}' % MODEL_PII)]
    )
    assert MODEL_PII not in out


def test_list_valued_content_parts_are_scanned(response_redaction_on):
    """The modern OpenAI shape sends content as a list of typed parts, not a string."""
    out = _drive(
        [
            _event(
                '{"choices":[{"index":0,"delta":{"content":[{"type":"text","text":"%s"}]}}]}'
                % MODEL_PII
            )
        ]
    )
    assert MODEL_PII not in out


def test_data_prefix_without_a_space_is_scanned(response_redaction_on):
    """The space after `data:` is optional in the SSE spec and clients strip it."""
    out = _drive(['data:{"choices":[{"index":0,"delta":{"content":"call %s"}}]}\n\n' % MODEL_PII])
    assert MODEL_PII not in out


def test_a_truncated_final_line_is_not_forwarded_raw(response_redaction_on):
    """A dropped connection leaves a line with no terminator. It used to be flushed as-is."""
    out = _drive(['data: {"choices":[{"index":0,"delta":{"content":"call %s"}}]}' % MODEL_PII])
    assert MODEL_PII not in out


def test_anthropic_message_start_is_scanned(response_redaction_on):
    """`message_start` matched none of the branches and fell through."""
    out = _drive(
        [
            _event(
                '{"type":"message_start","message":{"content":[{"type":"text","text":"contact %s"}]}}'
                % MODEL_PII
            )
        ]
    )
    assert MODEL_PII not in out


def test_legacy_text_choices_are_scanned(response_redaction_on):
    """Legacy completions put the text in `text`, not `delta`."""
    out = _drive([_event('{"choices":[{"index":0,"text":"call %s"}]}' % MODEL_PII)])
    assert MODEL_PII not in out


def test_non_dict_choice_entries_are_scanned(response_redaction_on):
    out = _drive([_event('{"choices":["raw %s"]}' % MODEL_PII)])
    assert MODEL_PII not in out


def test_an_unknown_provider_field_is_scanned(response_redaction_on):
    """The default must be to scan. A field nobody has heard of is still model output."""
    out = _drive([_event('{"choices":[{"index":0,"delta":{"some_new_field":"%s"}}]}' % MODEL_PII)])
    assert MODEL_PII not in out


def test_done_marker_is_left_alone(response_redaction_on):
    """Scanning by default must not mangle the end-of-stream marker."""
    out = _drive([_event('{"choices":[{"index":0,"delta":{"content":"hi"}}]}'), "data: [DONE]\n\n"])
    assert "data: [DONE]" in out


def test_structural_fields_are_left_alone(response_redaction_on):
    """An id or a model name is not PII, and rewriting it breaks clients."""
    out = _drive(
        [_event('{"id":"chatcmpl-abc123","model":"gpt-4o","choices":[{"index":0,"delta":{"content":"hi"}}]}')]
    )
    assert "chatcmpl-abc123" in out
    assert "gpt-4o" in out


def test_the_callers_own_value_is_still_restored(response_redaction_on):
    """Scanning by default must not redact the placeholder before it is restored."""

    async def main() -> str:
        vault = Vault(synthetic=False)
        token = vault.get_or_create_token("bob@example.com", "EMAIL")

        async def source():
            yield _event('{"choices":[{"index":0,"delta":{"content":"write to %s"}}]}' % token).encode()

        return "".join([c.decode() async for c in rehydrate_sse_stream(source(), vault)])

    out = asyncio.run(main())
    assert "bob@example.com" in out, "the caller's own value was not restored"


def test_done_marker_without_a_space_is_still_the_end_marker(response_redaction_on):
    """`data:[DONE]` is the same marker and must not be parsed as an event.

    Accepting the space-less `data:` prefix broke this once: a first version normalised
    the prefix by string replacement, which turned `data: [DONE]` into `data:  [DONE]`
    and let the end marker fall into the JSON parser. The watermark chunk moved and the
    stream ended in the wrong order.
    """
    out = _drive(
        [_event('{"choices":[{"index":0,"delta":{"content":"hi"}}]}'), "data:[DONE]\n\n"]
    )
    assert "[DONE]" in out
