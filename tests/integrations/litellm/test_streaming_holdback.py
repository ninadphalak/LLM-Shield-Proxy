"""LiteLLM's streaming transform, driven by the real ``SSERehydrationBuffer``.

This is the open item from ``GENERIC_GUARDRAIL_CONTRACT.md``. ``stream_holdback_chars``
was read out of LiteLLM's source but never exercised against the buffer that has to
satisfy it. The failure mode if the mapping is wrong is not an exception -- it is a
placeholder reaching the end user, which is the thing this design exists to prevent.
So the mapping is proved here rather than assumed.

LiteLLM's rule, from ``UnifiedLLMGuardrails._build_transform_chunk``::

    emitted_delta = text[len(already_emitted) : len(text) - holdback]

It fails the request closed with ``stream_transform_underflow`` when ``text`` is not a
forward extension of ``already_emitted``, because emitted bytes cannot be retracted.
And on the last round it forces ``holdback = 0``, so whatever the guardrail withheld
is emitted verbatim.

That last clause is what makes the shim call the Shield twice per round. If the
withheld region were the raw token, the final round would put the token on the wire
and every reply ending on a redacted value would finish by showing the user a
placeholder. So the withheld region is the *restored* text, and the withheld *length*
comes from the buffer.

One thing this file deliberately does not model: the shim never learns that a round is
the last one, because LiteLLM's request body carries no such field. Every round is
therefore identical here, and only LiteLLM's holdback handling changes at the end.
"""

from __future__ import annotations

import pytest

from llm_shield_proxy.engines.vault import vault_store
from llm_shield_proxy.streaming.streaming import SSERehydrationBuffer


@pytest.fixture()
def vault():
    """A real session vault, so rehydration behaves as it does in production."""
    return vault_store.get_vault(session_id="litellm-stream-test", virtual_key_id="litellm-stream-test")


@pytest.fixture()
def token(vault):
    """The stand-in the model is given in place of the real value."""
    return vault.get_or_create_token("jane.doe@example.com", "EMAIL_ADDRESS")


def _shim_round(vault, accumulated: str) -> tuple[str, int]:
    """One shim round, exactly as ``_restore_streaming`` performs it.

    The shim holds no per-stream state, so a fresh buffer stands in for the Shield's
    per-call buffer: re-feeding the whole accumulated text re-derives the withheld tail,
    and the complete-text path supplies the restored text.
    """
    buffer = SSERehydrationBuffer(vault)
    buffer.process_delta_text(accumulated, is_final=False)
    withheld = buffer.content_buffer
    return vault.rehydrate(accumulated, retention_length=0), len(withheld)


def _emit_as_litellm(text: str, already: str, holdback: int) -> str:
    """LiteLLM's slice, including the precondition it enforces on our returned text."""
    assert text.startswith(already), (
        "stream_transform_underflow: text returned was not a forward extension of what "
        f"was already streamed (already={len(already)} chars, text={len(text)})"
    )
    return text[len(already) : len(text) - holdback]


def _rounds(vault, snapshots: list[str]) -> list[str]:
    """Every prefix of the reply the client has seen, one entry per round."""
    already = ""
    seen: list[str] = []
    for index, snapshot in enumerate(snapshots):
        text, holdback = _shim_round(vault, snapshot)
        is_last = index == len(snapshots) - 1
        already += _emit_as_litellm(text, already, 0 if is_last else holdback)
        seen.append(already)
    return seen


def _char_by_char(model_output: str) -> list[str]:
    """The mean case: the model emits one character at a time, so every token straddles."""
    return [model_output[:end] for end in range(1, len(model_output) + 1)]


def test_a_token_split_across_rounds_is_never_emitted_in_fragments(vault, token):
    """Every byte the client saw must be a prefix of the finished reply.

    A leaked placeholder fragment would put bytes on the wire that the restored text
    does not contain, so this catches a fragment anywhere it could sit, without having
    to guess its length or offset.
    """
    model_output = f"Contact {token} now"
    seen = _rounds(vault, _char_by_char(model_output))
    final = seen[-1]

    assert final == "Contact jane.doe@example.com now"
    for already in seen:
        assert final.startswith(already), "the client received bytes the reply never contained"
        assert token not in already, "the raw placeholder reached the client"


def test_the_replay_reconstructs_the_fully_restored_reply(vault, token):
    """Nothing is dropped and nothing is duplicated across the round boundaries."""
    model_output = f"Contact {token} now"

    assert _rounds(vault, _char_by_char(model_output))[-1] == vault.rehydrate(model_output, retention_length=0)


def test_a_coarse_split_still_reconstructs(vault, token):
    """Chunk boundaries land wherever the model puts them, not one character at a time."""
    model_output = f"Here it is: {token}, ask again"
    snapshots = [model_output[:cut] for cut in (10, 24, 40) if cut < len(model_output)]
    snapshots.append(model_output)

    assert _rounds(vault, snapshots)[-1] == "Here it is: jane.doe@example.com, ask again"


def test_the_stream_ends_with_the_value_restored_not_a_placeholder(vault, token):
    """The reason the shim calls the Shield twice per round.

    LiteLLM forces holdback to 0 on the final round, so whatever was withheld is emitted
    verbatim. Were that the raw token, every reply ending on a redacted value would
    finish by showing the user a placeholder -- the exact failure this guardrail exists
    to prevent, stated in the in-tree PR's own TLDR.
    """
    model_output = f"Contact {token}"

    final = _rounds(vault, _char_by_char(model_output))[-1]

    assert final == "Contact jane.doe@example.com"
    assert token not in final


def test_the_naive_single_call_mapping_would_show_a_placeholder(vault, token):
    """Negative control for the two-call design, so it cannot be simplified away.

    Concatenating the stream endpoint's ``emitted + carry`` is the obvious single-call
    mapping, and it is wrong in exactly one place: the final round, where the holdback
    is forced to 0 and the withheld region is emitted verbatim. Without this, the test
    above would not be evidence that the two-call version is needed.
    """
    model_output = f"Contact {token}"
    snapshots = _char_by_char(model_output)
    already = ""

    for index, snapshot in enumerate(snapshots):
        buffer = SSERehydrationBuffer(vault)
        emitted = buffer.process_delta_text(snapshot, is_final=False)
        text = emitted + buffer.content_buffer
        holdback = len(buffer.content_buffer)
        already += _emit_as_litellm(text, already, 0 if index == len(snapshots) - 1 else holdback)

    assert token in already, "the naive mapping did not reproduce the defect it is cited for"
