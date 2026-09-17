"""A protected value split across two SSE events inside the same SIBLING JSON field.

The ordered content channel has a retention buffer, so a value cut in half by a chunk
boundary is still seen whole. Sibling JSON fields are scanned too, since 1.6.0, but each
event was scanned ALONE: `"ref 456-78"` in one event and `"-9012 filed"` in the next match
no detector separately, and a client that appends the field reassembles `456-78-9012`.
The v2 conformance profile measured this as the last 2 of 32 leaked cases, SSN and
USPHONE at the `same-path-join` evidence tier.

Retention is deliberately NOT the fix. An arbitrary sibling field has no defined
concatenation semantics -- a client may treat a repeated `note` as a replacement, not an
append -- so withholding its tail either delays a value the client discards or corrupts
one it keeps. Instead each JSON path remembers a bounded tail of what it ALREADY emitted,
a scratch probe joins that tail to the incoming fragment, and only the part of a
straddling span that falls inside the NEW fragment is redacted. Nothing is ever delayed,
so the change is invisible to a client with either semantics.

Every test here pins one half of that bargain: the leak is closed (1-5), or the bargain's
price is not paid anywhere it should not be -- bounded memory (7, 8), structural keys left
alone (6), the content channel untouched (9), and constant cost on the hot path (10).
"""

from __future__ import annotations

import time

import pytest

from llm_shield_proxy.core.config import settings
from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.streaming import streaming as streaming_module
from llm_shield_proxy.streaming.streaming import (
    MAX_SIBLING_PATHS,
    SSERehydrationBuffer,
    _redact_sibling_strings,
    _SiblingPathTails,
    rehydrate_sse_stream,
)

# Split so that NEITHER half matches on its own. That is the whole premise: if the prefix
# matched alone the existing per-event scan would already have redacted it at event 1, and
# there would be no cross-boundary case to handle.
SSN_HEAD, SSN_TAIL = "ref 456-78", "-9012 filed"
SSN_WHOLE = "456-78-9012"
PHONE_HEAD, PHONE_TAIL = "call 415-555-", "0199 ext 4"
PHONE_WHOLE = "415-555-0199"


@pytest.fixture
def response_redaction_on():
    previous = settings.ENABLE_RESPONSE_PII_REDACTION
    settings.ENABLE_RESPONSE_PII_REDACTION = True
    try:
        yield
    finally:
        settings.ENABLE_RESPONSE_PII_REDACTION = previous


def _buffer() -> SSERehydrationBuffer:
    return SSERehydrationBuffer(Vault(synthetic=False))


def _drive_events(events: list[dict], buffer=None, tails=None) -> list[dict]:
    """Runs the sibling scan over a sequence of already-parsed SSE event objects.

    This is the exact call the stream makes at its sibling-scan site, minus the SSE
    framing, so the assertions are about redaction rather than about wire format.
    """
    buffer = buffer or _buffer()
    tails = tails if tails is not None else _SiblingPathTails()
    return [_redact_sibling_strings(event, buffer, tails=tails) for event in events]


def _note_event(text: str, *, key: str = "note", index: int = 0) -> dict:
    return {"choices": [{"index": index, "delta": {"content": "x", key: text}}]}


def _notes(scanned: list[dict], *, key: str = "note", choice: int = 0) -> list[str]:
    return [event["choices"][choice]["delta"][key] for event in scanned]


async def _collect_stream(raw_stream, vault) -> str:
    pieces = []
    async for chunk in rehydrate_sse_stream(raw_stream, vault):
        pieces.append(chunk.decode("utf-8"))
    return "".join(pieces)


# --------------------------------------------------------------------------------------
# 1-5: the leak itself
# --------------------------------------------------------------------------------------


def test_value_split_across_two_events_is_redacted_in_the_second_fragment(response_redaction_on):
    """The measured leak: the `same-path-join` case the v2 profile still scored.

    Neither fragment matches alone, so neither per-event scan fires and the client used to
    reassemble the whole SSN. The completing fragment is what creates the value, so
    redacting the completing fragment is what prevents the reconstruction.
    """
    scanned = _drive_events([_note_event(SSN_HEAD), _note_event(SSN_TAIL)])

    reassembled = "".join(_notes(scanned))
    assert SSN_WHOLE not in reassembled, "an appending client still reconstructs the SSN"
    assert "[SSN_REDACTED]" in _notes(scanned)[1]


def test_the_first_fragment_is_emitted_unchanged(response_redaction_on):
    """Nothing is delayed, withheld or rewritten on the way out. That is the design.

    A sibling field has no defined concatenation semantics, so a retention buffer here
    would either delay a value a "last value wins" client discards, or corrupt one it
    keeps. This pins that the fix never reaches backwards into an event already framed.
    """
    scanned = _drive_events([_note_event(SSN_HEAD), _note_event(SSN_TAIL)])

    assert _notes(scanned)[0] == SSN_HEAD, "the already-emitted prefix was altered"
    # And the documented residue is honest about itself: the prefix does survive.
    assert "456-78" in _notes(scanned)[0]


def test_a_value_split_across_three_events_is_caught_at_the_fragment_that_completes_it(
    response_redaction_on,
):
    """Two events is the easy case; three is what proves the tail ACCUMULATES.

    A tail that remembered only the previous fragment would probe `"78-" + "9012"` at
    event 3, see nothing, and ship the value. The tail is the bounded running tail of
    everything the path emitted, so the probe is `"ref 456-78-" + "9012"` and fires
    exactly once, on the fragment that completes the value and not before.
    """
    scanned = _drive_events(
        [_note_event("ref 456-"), _note_event("78-"), _note_event("9012")]
    )
    notes = _notes(scanned)

    assert notes[0] == "ref 456-", "the first fragment was not emitted verbatim"
    assert notes[1] == "78-", "a value was redacted before anything completed it"
    assert "[SSN_REDACTED]" in notes[2]
    assert SSN_WHOLE not in "".join(notes)


def test_two_paths_carrying_two_split_values_do_not_contaminate_each_other(
    response_redaction_on,
):
    """Interleaved fields must be tracked separately, and both must still be caught.

    A single shared tail would hand one field the other's text, which both misses real
    joins and invents joins no client performs. Keying by JSON path is what prevents it.
    """
    events = [
        {"choices": [{"index": 0, "delta": {"content": "x", "note": SSN_HEAD, "memo": PHONE_HEAD}}]},
        {"choices": [{"index": 0, "delta": {"content": "y", "note": SSN_TAIL, "memo": PHONE_TAIL}}]},
    ]
    scanned = _drive_events(events)

    notes = _notes(scanned, key="note")
    memos = _notes(scanned, key="memo")
    assert "[SSN_REDACTED]" in notes[1]
    assert "[PHONE_REDACTED]" in memos[1]
    assert SSN_WHOLE not in "".join(notes)
    assert PHONE_WHOLE not in "".join(memos)
    # Neither field was redacted on the strength of the other's text.
    assert notes[0] == SSN_HEAD and memos[0] == PHONE_HEAD


def test_halves_landing_in_different_paths_are_not_joined(response_redaction_on):
    """Redacting a join no client performs is a fidelity bug, not extra safety.

    `note` in one event and `memo` in the next are two different fields. Nothing
    concatenates them, so there is no reconstruction to prevent, and rewriting the second
    one would destroy a value the caller asked for. The harness tiers this as
    `cross-field-join` for the same reason: it is not the same evidence.
    """
    events = [
        {"choices": [{"index": 0, "delta": {"content": "x", "note": SSN_HEAD}}]},
        {"choices": [{"index": 0, "delta": {"content": "y", "memo": SSN_TAIL}}]},
    ]
    scanned = _drive_events(events)

    assert scanned[0]["choices"][0]["delta"]["note"] == SSN_HEAD
    assert scanned[1]["choices"][0]["delta"]["memo"] == SSN_TAIL, (
        "two unrelated fields were joined and a value was destroyed"
    )


# --------------------------------------------------------------------------------------
# 6-8: the bounds, which are invariants and not preferences
# --------------------------------------------------------------------------------------


def test_structural_keys_are_never_tracked(response_redaction_on):
    """An `id`, a `model` or a `finish_reason` is not PII, and it is stable across events.

    Tracking one would join a value to itself on every event and produce a permanent
    match, mangling the fields that tell a client what the response IS. The skip happens
    BEFORE the path is extended, so a structural key cannot reach the tail map at all.
    """
    tails = _SiblingPathTails()
    events = [
        {"id": "chatcmpl-456-78", "model": "gpt-456-78", "choices": [{"index": 0, "delta": {"content": "x", "note": "ok"}}]},
        {"id": "chatcmpl-456-78", "model": "-9012", "choices": [{"index": 0, "delta": {"content": "y", "note": "ok"}}]},
    ]
    scanned = _drive_events(events, tails=tails)

    assert scanned[1]["id"] == "chatcmpl-456-78"
    assert scanned[1]["model"] == "-9012", "a structural key was rewritten"
    tracked = tails.tracked_paths
    assert tracked == (".choices[0].delta.note",), f"unexpected tracked paths: {tracked}"


def test_exceeding_the_path_cap_evicts_and_records_the_eviction(response_redaction_on):
    """An unbounded map keyed by attacker-chosen JSON paths is the leak this repo forbids.

    So the map has a hard cap, evicts least-recently-seen, and COUNTS what it dropped. A
    silently evicted path silently stops catching splits on that path, which is a silent
    reduction in scan coverage; the counter is what makes it visible in the response
    metrics instead.
    """
    from llm_shield_proxy.observability.metrics import (
        llm_shield_sse_sibling_paths_evicted_total as evicted_metric,
    )

    before = evicted_metric._value.get()
    tails = _SiblingPathTails(max_paths=4)
    vault = Vault(synthetic=False)
    for i in range(7):
        tails.scan(f".choices[0].delta.f{i}", "harmless", vault)

    assert len(tails) == 4, "the cap did not hold"
    assert tails.evictions == 3
    assert evicted_metric._value.get() > before, "the eviction was not recorded"
    # LRU, not arbitrary: the three oldest went and the four most recent stayed.
    assert tails.tracked_paths == tuple(f".choices[0].delta.f{i}" for i in range(3, 7))


def test_ten_thousand_fresh_paths_stay_bounded(response_redaction_on):
    """The hostile shape: an upstream that invents a new JSON key on every event.

    Memory must be a function of the cap and the retention window, never of how many
    paths the upstream chose to name. Both the entry count and the retained characters
    are asserted, because a bounded key count with unbounded values is the same leak.
    """
    tails = _SiblingPathTails()
    vault = Vault(synthetic=False)
    for i in range(10_000):
        tails.scan(f".choices[0].delta.k{i}", "a harmless fragment of text", vault)

    assert len(tails) == MAX_SIBLING_PATHS
    assert tails.evictions == 10_000 - MAX_SIBLING_PATHS
    retained = sum(len(tails._tails[p]) for p in tails.tracked_paths)
    assert retained <= MAX_SIBLING_PATHS * settings.RESPONSE_PII_SCAN_WINDOW


# --------------------------------------------------------------------------------------
# 9-10: what must not change
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_retention_buffer_still_owns_delta_content(response_redaction_on):
    """The ordered channel is not this code's business, and double-redacting it is fatal.

    `delta.content` is already redacted and rehydrated in order by its retention window.
    Scanning it again would redact the caller's own restored values back out -- fidelity
    1.00 to 0.00 -- which is exactly how the `_BUFFERED_CHANNEL_KEYS` skip was found. This
    pins both halves: content is never a tracked path, and a value split across the
    content channel is still caught by the buffer alone.
    """
    vault = Vault(synthetic=False)
    token = vault.get_or_create_token("sarah@skynet.com", "EMAIL")

    async def split_content_stream():
        first = (
            b'data: {"choices":[{"index":0,"delta":{"content":"mine '
            + token.encode()
            + b' theirs nuwpc","note":"ok"}}]}\n'
        )
        yield first
        yield b'data: {"choices":[{"index":0,"delta":{"content":"bba@example.com done","note":"ok"}}]}\n'
        yield b"data: [DONE]\n"

    output = await _collect_stream(split_content_stream(), vault)

    assert "sarah@skynet.com" in output, "the caller's own value was not restored"
    assert "nuwpcbba@example.com" not in output, "a model value straddling the boundary leaked"
    assert "[EMAIL_1]" not in output, "a placeholder reached the client"

    # And directly: the content key is never entered into the path map.
    tails = _SiblingPathTails()
    _drive_events([_note_event("hello")], tails=tails)
    assert not any(p.endswith(".content") for p in tails.tracked_paths)


def test_the_cross_boundary_probe_is_constant_cost_per_path(response_redaction_on):
    """Hot path: one dict lookup and one bounded slice, whatever the field's size.

    A probe built from the whole retained field and the whole incoming fragment would make
    the per-event cost grow with the response, which on the streaming path is a latency
    regression rather than a correctness one. Both halves are clipped to the retention
    window -- the same bound the content channel already holds back -- so the added work
    is fixed. The wall-clock ratio is a coarse backstop; the probe-length assertion is the
    part that actually pins the regression.
    """
    from llm_shield_proxy.engines.pii_engine import pii_engine

    window = settings.RESPONSE_PII_SCAN_WINDOW
    probe_lengths: list[int] = []
    real_detect = pii_engine.detect_spans

    def recording_detect(text, *args, **kwargs):
        probe_lengths.append(len(text))
        return real_detect(text, *args, **kwargs)

    big = "lorem ipsum dolor sit amet " * 2_000  # ~54 KB on one sibling field
    tails = _SiblingPathTails()
    vault = Vault(synthetic=False)

    pii_engine.detect_spans = recording_detect
    try:
        tails.scan(".choices[0].delta.note", big, vault)
        probe_lengths.clear()  # the first event has no tail, so it runs no probe
        tails.scan(".choices[0].delta.note", big, vault)
    finally:
        pii_engine.detect_spans = real_detect

    assert probe_lengths, "the second fragment ran no cross-boundary probe at all"
    assert max(probe_lengths) <= 2 * window, (
        f"probe grew with the field: {max(probe_lengths)} chars for a {len(big)}-char field"
    )

    # Coarse throughput backstop on a realistic event stream, tracked vs the pre-change
    # path (`tails=None` is exactly the 1.6.0 behaviour). Best-of-5 on both sides so one
    # scheduling hiccup cannot fail the run.
    events = [_note_event(f"status update {i} for the current request", index=0) for i in range(200)]

    def _run(with_tails):
        buffer = _buffer()
        tracker = _SiblingPathTails() if with_tails else None
        best = float("inf")
        for _ in range(5):
            started = time.perf_counter()
            for event in events:
                _redact_sibling_strings(event, buffer, tails=tracker)
            best = min(best, time.perf_counter() - started)
        return best

    baseline = _run(False)
    tracked = _run(True)
    assert tracked <= baseline * 3.0 + 0.05, (
        f"sibling scan slowed from {baseline:.4f}s to {tracked:.4f}s per 200 events"
    )


def test_the_module_level_cap_is_what_the_stream_uses():
    """The cap is a module constant so a deployment can be reasoned about and a test can
    monkeypatch it. A stream that built its own number would make both impossible."""
    assert streaming_module.MAX_SIBLING_PATHS == MAX_SIBLING_PATHS
    assert _SiblingPathTails()._max_paths == MAX_SIBLING_PATHS
