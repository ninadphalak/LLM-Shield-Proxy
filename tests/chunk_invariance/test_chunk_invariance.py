"""The assertion against three filters whose behaviour is known: one correct, two that leak."""

import asyncio

import pytest
from chunk_invariance import (
    ChunkInvarianceError,
    all_splits,
    assert_chunk_invariant,
    assert_chunk_invariant_async,
    from_async,
    per_chunk,
    two_part_splits,
)
from chunk_invariance.examples import design_a, design_b, hold_back, redact

TEXT = "use sk-AbCd1234 now"
TWO_KEYS = "a sk-AAAAAAAA and sk-BBBBBBBBsk-CCCCCCCC then sk-DDDD"


def test_two_part_splits_are_every_single_cut_in_order():
    assert list(two_part_splits("abcd")) == [["a", "bcd"], ["ab", "cd"], ["abc", "d"]]
    assert list(two_part_splits("a")) == []


def test_all_splits_counts_and_orders_by_fewest_parts():
    splits = list(all_splits("abcde"))
    assert len(splits) == 2 ** 4
    assert splits[0] == ["abcde"]
    assert [len(s) for s in splits] == sorted(len(s) for s in splits)
    assert all("".join(s) == "abcde" and all(s) for s in splits)
    assert len(list(all_splits("abcde", 2))) == 1 + 4
    assert list(all_splits("", 3)) == [[""]]
    with pytest.raises(ValueError):
        list(all_splits("abc", 0))


def test_hold_back_filter_passes_every_split_of_a_short_input():
    assert assert_chunk_invariant(hold_back, redact, "x sk-AbCd1234y", splits="all") == 2 ** 13


def test_hold_back_filter_passes_three_part_splits_with_adjacent_and_trailing_keys():
    checked = assert_chunk_invariant(hold_back, redact, TWO_KEYS, splits=3)
    n = len(TWO_KEYS)
    assert checked == 1 + (n - 1) + (n - 1) * (n - 2) // 2


def test_design_a_fails_and_the_error_names_the_split():
    with pytest.raises(ChunkInvarianceError) as caught:
        assert_chunk_invariant(design_a, redact, TEXT)
    error = caught.value
    assert error.chunks == ["use s", "k-AbCd1234 now"]
    assert error.expected == "use [KEY] now"
    assert error.actual == TEXT
    message = str(error)
    assert "split:    ['use s', 'k-AbCd1234 now'] (cut at 5)" in message
    assert "whole:    'use [KEY] now'" in message
    assert "streamed: 'use sk-AbCd1234 now'" in message


def test_design_b_fails_with_a_leak_and_a_corrupted_tail():
    with pytest.raises(ChunkInvarianceError) as caught:
        assert_chunk_invariant(design_b, redact, TEXT)
    error = caught.value
    assert error.chunks == ["use s", "k-AbCd1234 now"]
    # The "s" left with the first chunk; redacting the join then cut the wrong prefix off.
    assert error.actual == "use sKEY] now"


def test_design_b_passes_the_unsplit_input_which_is_why_it_looks_fixed():
    assert assert_chunk_invariant(design_b, redact, TEXT, splits=[[TEXT]]) == 1


def test_the_minimal_split_is_reported_before_larger_ones():
    with pytest.raises(ChunkInvarianceError) as caught:
        assert_chunk_invariant(design_a, redact, TEXT, splits=4)
    assert len(caught.value.chunks) == 2


def test_explicit_splits_must_join_back_to_the_input():
    with pytest.raises(ValueError):
        assert_chunk_invariant(hold_back, redact, TEXT, splits=[["use", "sk"]])


def test_all_is_refused_for_long_inputs():
    with pytest.raises(ValueError, match="pass an int"):
        assert_chunk_invariant(hold_back, redact, "x" * 17, splits="all")


def test_unknown_split_modes_are_refused():
    with pytest.raises(ValueError):
        assert_chunk_invariant(hold_back, redact, TEXT, splits="every")
    with pytest.raises(TypeError):
        assert_chunk_invariant(hold_back, redact, TEXT, splits=True)


def test_missing_flush_is_caught():
    no_flush = per_chunk(lambda chunk, state: chunk[:-1] if chunk else chunk)
    with pytest.raises(ChunkInvarianceError):
        assert_chunk_invariant(no_flush, lambda t: t[:-1], "abc")


def test_per_chunk_gives_every_stream_fresh_state():
    seen = []

    def process(chunk, state):
        state.setdefault("chunks", []).append(chunk)
        seen.append(len(state["chunks"]))
        return chunk

    assert_chunk_invariant(per_chunk(process), lambda t: t, "abc")
    # One chunk, then two chunks twice: counts restart at 1 for each stream.
    assert seen == [1, 1, 2, 1, 2]


def test_bytes_inputs_are_joined_as_bytes():
    def upper_stream(chunks):
        for chunk in chunks:
            yield chunk.upper()

    assert assert_chunk_invariant(upper_stream, bytes.upper, b"abc") == 3


async def _async_design_a(chunks):
    async for chunk in chunks:
        yield redact(chunk)


async def _async_hold_back(chunks):
    state = {}
    process, flush = hold_back_parts()
    async for chunk in chunks:
        yield process(chunk, state)
    yield flush(state)


def hold_back_parts():
    from chunk_invariance.examples import _hold_back_flush, _hold_back_process

    return _hold_back_process, _hold_back_flush


def test_from_async_adapts_an_async_generator():
    assert_chunk_invariant(from_async(_async_hold_back), redact, TEXT)
    with pytest.raises(ChunkInvarianceError):
        assert_chunk_invariant(from_async(_async_design_a), redact, TEXT)


def test_async_assertion_runs_inside_an_event_loop():
    async def whole(text):
        return redact(text)

    async def scenario():
        assert await assert_chunk_invariant_async(_async_hold_back, whole, TEXT) == len(TEXT)
        with pytest.raises(ChunkInvarianceError):
            await assert_chunk_invariant_async(_async_design_a, redact, TEXT)
        with pytest.raises(RuntimeError, match="assert_chunk_invariant_async"):
            assert_chunk_invariant(from_async(_async_hold_back), redact, TEXT)

    asyncio.run(scenario())


def test_hypothesis_strategies_find_the_design_a_leak():
    pytest.importorskip("hypothesis")
    from chunk_invariance.strategies import splits_of, text_and_splits
    from hypothesis import given, settings
    from hypothesis import strategies as st

    @settings(max_examples=200, deadline=None, database=None)
    @given(splits_of(TEXT, max_parts=4))
    def hold_back_holds(chunks):
        assert "".join(chunks) == TEXT and all(chunks) and len(chunks) <= 4
        assert_chunk_invariant(hold_back, redact, TEXT, splits=[chunks])

    hold_back_holds()

    @settings(max_examples=200, deadline=None, database=None)
    @given(splits_of(TEXT))
    def design_a_holds(chunks):
        assert_chunk_invariant(design_a, redact, TEXT, splits=[chunks])

    with pytest.raises(ChunkInvarianceError):
        design_a_holds()

    @settings(max_examples=50, deadline=None, database=None)
    @given(text_and_splits(st.text(alphabet="sk-AB12 ", max_size=20), max_parts=3))
    def pairs(pair):
        text, chunks = pair
        assert "".join(chunks) == text
        assert_chunk_invariant(hold_back, redact, text, splits=[chunks])

    pairs()
