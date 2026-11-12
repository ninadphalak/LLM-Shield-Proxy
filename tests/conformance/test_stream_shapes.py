"""Stream-shape partitions: token-aligned, punctuation-cut, per-character and many-part.

The v2 profile cuts an injected value at its midpoint, or at every single or pair of offsets.
Providers deliver values as token streams instead, and a gateway can be right for every
single cut and still leak when a value arrives in many small pieces, or when a piece ends
right after a mark inside the value. `pii_leak_benchmark.stream_shapes` runs those shapes as
a separate pass, so the pinned v2 scorer and its digest do not move.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pii-leak-benchmark"))

from pii_leak_benchmark import stream_shapes as ss  # noqa: E402
from pii_leak_benchmark import v2_emitter as v2  # noqa: E402

EMAIL = "nuwpcbba@example.com"
CASE = {
    "entity": "EMAIL",
    "encoding": "plain",
    "fragmentation": "adversarial",
    "carrier": "sse-delta-content",
    "request_site": "chat-content",
}


@pytest.mark.parametrize("family", ss.ALL_FAMILIES)
@pytest.mark.parametrize("value", [EMAIL, "219-09-9999", "4111-1111-1111-1111", "nuwpcbba%40example.com"])
def test_every_partition_is_a_valid_v2_partition(family, value):
    partitions = ss.family_partitions(value, family)
    assert partitions == ss.family_partitions(value, family), "must be deterministic"
    assert len(partitions) == len(set(partitions))
    for cuts in partitions:
        assert list(cuts) == sorted(cuts) and all(0 < c < len(value) for c in cuts)
        # The capture's own check: pieces join back, none empty, none holds the whole value.
        assert "".join(v2._partition_pieces(value, cuts)) == value


def test_token_aligned_cuts_follow_word_digit_and_punctuation_boundaries():
    assert ss.simple_tokens(EMAIL) == ["nuwpcbba", "@", "example", ".", "com"]
    assert ss.simple_tokens("219-09-9999") == ["219", "-", "09", "-", "999", "9"]
    one_per_event = ss.family_partitions(EMAIL, "token-aligned")[0]
    assert one_per_event == (8, 9, 16, 17)


def test_punctuation_cut_ends_a_piece_right_after_a_mark_inside_the_value():
    partitions = ss.family_partitions(EMAIL, "punctuation-cut")
    dot = EMAIL.index(".") + 1
    assert (dot,) in partitions
    assert all(dot in cuts for cuts in partitions)
    assert ss.family_partitions("219-09-9999", "punctuation-cut") == []


def test_n_part_has_exactly_n_pieces_and_per_character_has_one_each():
    sizes = {len(cuts) + 1 for cuts in ss.family_partitions(EMAIL, "n-part")}
    assert sizes == {3, 4, 6, 8}
    assert ss.family_partitions(EMAIL, "per-character") == [tuple(range(1, len(EMAIL)))]


def test_unknown_family_is_refused():
    with pytest.raises(ValueError, match="stream-union"):
        ss.family_partitions(EMAIL, "midpoint")


def test_the_pinned_v2_scorer_is_untouched():
    """This check reads the v2 scorer; it must not be part of it."""
    assert "stream_shapes" not in v2.inspector_source()
    assert not any(name.startswith("family_partitions") for name in v2._INSTRUMENTED)


@pytest.mark.parametrize("family", ["token-aligned", "per-character", "n-part"])
def test_chunk_local_leaks_and_the_decoding_retention_policy_does_not(family):
    segments = v2.build_segments("a1b2c3d4e5f60001")
    leaky = ss.probe_case(segments, "chunk-local", CASE, family)
    assert leaky["leaked"] and leaky["transport_error"] is None
    sound = ss.probe_case(segments, "retention-plus-decoding", CASE, family)
    assert not sound["leaked"] and sound["transport_error"] is None
    assert sound["partitions_tried"] == len(ss.family_partitions(segments.injection["EMAIL"], family))


def test_report_and_cli(tmp_path, capsys):
    out = tmp_path / "shapes.json"
    code = ss.main(
        [
            "--only",
            "retention-plus-decoding",
            "--family",
            "token-aligned",
            "--seed",
            "a1b2c3d4e5f60001",
            "--json-out",
            str(out),
        ]
    )
    assert code == 0
    import json

    report = json.loads(out.read_text())[0]
    assert report["schema"] == ss.STREAM_SHAPES_SCHEMA_ID
    assert report["cases_leaked"] == 0 and report["cases_scored"] == 16
    assert len(report["instrument"]["stream_shapes_sha256"]) == 16
    assert "retention-plus-decoding" in capsys.readouterr().out
