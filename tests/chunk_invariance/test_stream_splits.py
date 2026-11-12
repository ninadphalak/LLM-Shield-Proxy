"""Stream-shaped splits: token streams, chunks ending after punctuation, many-part splits."""

import sys

import pytest
from chunk_invariance import (
    ChunkInvarianceError,
    all_splits,
    assert_chunk_invariant,
    fixed_size_splits,
    n_part_splits,
    punctuation_splits,
    simple_tokens,
    stream_splits,
    token_splits,
    tokenize,
)
from chunk_invariance.core import NAMED_SPLITS, _resolve_splits
from chunk_invariance.examples import design_a, hold_back, lookahead_one, redact

TEXT = "use sk-AbCd1234 now"
DOTTED = "Sure. Call 555.867.5309 or try 10.0.0.5? Fine!"


def _joins(text, splits):
    splits = list(splits)
    assert splits
    for chunks in splits:
        assert text[:0].join(chunks) == text
        assert all(chunks) or chunks == [text]
    return splits


def test_simple_tokens_join_back_and_group_digits_like_a_byte_pair_tokenizer():
    tokens = simple_tokens(DOTTED)
    assert "".join(tokens) == DOTTED
    assert tokens[:5] == ["Sure", ".", " Call", " ", "555"]
    assert all(len(t) <= 3 for t in tokens if t.isdigit())
    assert simple_tokens("") == []


def test_tokenize_without_tiktoken_falls_back_or_says_what_to_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "tiktoken", None)
    assert tokenize(DOTTED) == simple_tokens(DOTTED)
    assert tokenize(DOTTED, None) == simple_tokens(DOTTED)
    with pytest.raises(ImportError, match="chunk-invariance\\[tokens\\]"):
        tokenize(DOTTED, "o200k_base")


def test_tokenize_auto_falls_back_when_the_encoding_cannot_be_loaded(monkeypatch):
    """tiktoken downloads its encoding file on first use; offline that raises. The default
    check must still run, on the built-in tokenizer."""
    import types

    def unavailable(name):
        raise OSError("no network")

    monkeypatch.setitem(sys.modules, "tiktoken", types.SimpleNamespace(get_encoding=unavailable))
    assert tokenize(DOTTED) == simple_tokens(DOTTED)
    assert assert_chunk_invariant(hold_back, redact, TEXT) > len(TEXT)
    with pytest.raises(OSError):
        tokenize(DOTTED, "o200k_base")


def test_tokenize_with_tiktoken_uses_real_token_boundaries():
    pytest.importorskip("tiktoken")
    tokens = tokenize(DOTTED, "cl100k_base")
    assert "".join(tokens) == DOTTED
    assert "555" in tokens and "." in tokens


def test_token_splits_are_one_token_per_chunk_then_batches():
    splits = _joins(DOTTED, token_splits(DOTTED, encoding="simple"))
    assert splits[0] == simple_tokens(DOTTED)
    assert len(splits) == 4
    assert [len(s) for s in splits] == sorted((len(s) for s in splits), reverse=True)


def test_punctuation_splits_end_a_chunk_right_after_a_mark_inside_a_value():
    splits = _joins(DOTTED, punctuation_splits(DOTTED, encoding="simple"))
    for chunks in splits:
        assert any(chunks[i][-1] in ".?!" and not chunks[i + 1][0].isspace() for i in range(len(chunks) - 1)), chunks
    # Marks followed by a space end a sentence, not a value, so no split is made for them.
    assert ["Sure.", " Call 555.867.5309 or try 10.0.0.5? Fine!"] not in splits
    assert ["Sure. Call 555.", "867.5309 or try 10.0.0.5? Fine!"] in splits
    assert ["Sure. Call ", "555.", "867.5309 or try 10.0.0.5? Fine!"] in splits
    assert list(punctuation_splits("no marks here. None at all!")) == []


def test_fixed_size_and_n_part_splits():
    sizes = _joins(TEXT, fixed_size_splits(TEXT))
    assert sizes[0] == list(TEXT)
    n_part = _joins(TEXT, n_part_splits(TEXT, parts=(3, 5), samples=4))
    assert [len(s) for s in n_part] == [3] * 4 + [5] * 4
    assert n_part == list(n_part_splits(TEXT, parts=(3, 5), samples=4)), "must be deterministic"
    assert list(n_part_splits("ab", parts=(3,))) == []
    with pytest.raises(ValueError):
        list(n_part_splits(TEXT, parts=(1,)))
    with pytest.raises(ValueError):
        list(fixed_size_splits(TEXT, sizes=(0,)))


def test_stream_splits_have_no_duplicates():
    splits = _joins(DOTTED, stream_splits(DOTTED, encoding="simple"))
    keys = [tuple(len("".join(s[: i + 1])) for i in range(len(s) - 1)) for s in splits]
    assert len(keys) == len(set(keys))


def test_default_runs_every_single_cut_first_then_the_stream_shapes():
    default = list(_resolve_splits(TEXT, "default"))
    two_part = list(all_splits(TEXT, 2))
    assert default[: len(two_part)] == two_part
    assert len(default) > len(two_part)
    assert any(len(chunks) > 3 for chunks in default)


def test_lookahead_one_passes_every_single_cut_and_fails_the_stream_shapes():
    """A filter that is right whenever a value spans at most two chunks."""
    assert assert_chunk_invariant(lookahead_one, redact, TEXT, splits="all-two-part") == len(TEXT)
    for mode in ("default", "tokens", "streams", "fixed-size", "n-part"):
        with pytest.raises(ChunkInvarianceError) as caught:
            assert_chunk_invariant(lookahead_one, redact, TEXT, splits=mode)
        assert "sk-AbCd1234" in caught.value.actual
        assert len(caught.value.chunks) >= 3


@pytest.mark.parametrize("mode", [m for m in NAMED_SPLITS if m != "all"])
def test_hold_back_passes_every_named_set_and_design_a_fails_it(mode):
    text = TEXT if mode != "punctuation" else "use sk-Ab.d1234 now and sk-AbCd1234!"
    assert assert_chunk_invariant(hold_back, redact, text, splits=mode) >= 1
    with pytest.raises(ChunkInvarianceError):
        assert_chunk_invariant(design_a, redact, text, splits=mode)


def test_unknown_named_set_lists_the_known_ones():
    with pytest.raises(ValueError, match="streams"):
        assert_chunk_invariant(hold_back, redact, TEXT, splits="everything")


def test_bytes_inputs_get_the_shapes_that_apply_to_bytes():
    splits = _joins(b"a.bc", _resolve_splits(b"a.bc", "default"))
    assert [b"a.", b"bc"] in splits
    assert [b"a", b".", b"b", b"c"] in splits
