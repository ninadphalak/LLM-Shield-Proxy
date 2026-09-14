"""The partition oracle, the FIDE axis, and the properties that make them measurements.

Every test here names the thing it stops. A partition oracle is a machine for generating
thousands of near-identical requests, and there are exactly four ways for it to lie:

  * enumerate the wrong set (too few, too many, or duplicates), so a "worst case" is not
    over what it says it is;
  * change the value while splitting it, so what leaked is not what the corpus contains;
  * leave the whole value in one piece, so a "fragmentation" leak is an ordinary one;
  * abort or truncate and score the silence as containment.
"""

from __future__ import annotations

import itertools
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pii-leak-benchmark"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benchmarks"))

import fide_numeric_audit as numeric_audit  # noqa: E402
import fide_sweep  # noqa: E402
from fide_sweep import claim_scoped_metrics, publishable_fide_files  # noqa: E402
from llm_guard_exhaustive import publishable_llm_guard_files  # noqa: E402
from pii_leak_benchmark import fide_emitter as fide  # noqa: E402
from pii_leak_benchmark import needle_registry as nr  # noqa: E402
from pii_leak_benchmark.v2_emitter import (  # noqa: E402
    AXES,
    DEFAULT_PARTITION_CAP,
    ORACLES,
    PARTITION_FAMILIES,
    RunResult,
    _all_pairs,
    _discordance,
    _encode,
    _family_partitions,
    _fragmentation_strategy_label,
    _pairs_of,
    _partition_oracle_block,
    _partition_pieces,
    _twin_key,
    build_segments,
    covering_array,
    injection_partitions,
)

SEED = "a1b2c3d4e5f60001"


def _case(entity: str, **over: str) -> dict[str, str]:
    case = {
        "entity": entity,
        "encoding": "plain",
        "fragmentation": "adversarial",
        "carrier": "sse-delta-content",
        "request_site": "chat-content",
    }
    case.update(over)
    return case


# --------------------------------------------------------------------------------------
# Enumeration: the right set, exactly once
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("length", [3, 4, 11, 12, 19, 24, 40, 78])
def test_two_part_enumeration_covers_every_internal_split_exactly_once(length: int) -> None:
    """N-1 partitions, each a distinct internal offset, none repeated and none missing."""
    rendered = "x" * length
    parts = _family_partitions(rendered, "exhaustive-2-part")
    assert len(parts) == length - 1
    assert len(set(parts)) == len(parts), "a duplicate partition inflates the attempted count"
    assert {p[0] for p in parts} == set(range(1, length))


@pytest.mark.parametrize("length", [3, 4, 11, 12, 19, 24, 40, 78])
def test_three_part_enumeration_is_exactly_n_minus_one_choose_two(length: int) -> None:
    """choose(N-1, 2) partitions, no duplicates, every pair of distinct internal cuts."""
    rendered = "x" * length
    parts = _family_partitions(rendered, "exhaustive-3-part")
    assert len(parts) == math.comb(length - 1, 2)
    assert len(set(parts)) == len(parts)
    assert set(parts) == {
        (i, j) for i, j in itertools.combinations(range(1, length), 2)
    }
    assert all(i < j for i, j in parts), "cuts must be ordered and distinct"


def test_the_two_families_are_disjoint_so_the_union_does_not_double_count() -> None:
    two = set(_family_partitions("x" * 20, "exhaustive-2-part"))
    three = set(_family_partitions("x" * 20, "exhaustive-3-part"))
    assert not (two & three)


def test_the_union_oracle_enumerates_exactly_the_two_families_together() -> None:
    segments = build_segments(SEED)
    case = _case("SSN")
    parts, families, attempted, capped = injection_partitions(
        segments, case, oracle="union-worst-case"
    )
    rendered = _encode(segments.injection["SSN"], "plain")
    expected = _family_partitions(rendered, "exhaustive-2-part") + _family_partitions(
        rendered, "exhaustive-3-part"
    )
    assert parts == expected
    assert len(families) == len(parts)
    assert attempted == {
        "exhaustive-2-part": len(rendered) - 1,
        "exhaustive-3-part": math.comb(len(rendered) - 1, 2),
    }
    assert capped == {f: False for f in PARTITION_FAMILIES}
    # Every partition is attributed to the family that produced it, or the union cannot
    # be decomposed and `families` is decoration.
    for cuts, family in zip(parts, families):
        assert len(cuts) == (1 if family == "exhaustive-2-part" else 2)


def test_a_single_chunk_case_enumerates_no_partitions_at_all() -> None:
    """The baseline arm is one UNCUT request. Counting it as a split is the 252 erratum."""
    segments = build_segments(SEED)
    parts, families, attempted, capped = injection_partitions(
        segments, _case("SSN", fragmentation="single_chunk"), oracle="union-worst-case"
    )
    assert parts == [()]
    assert families == []
    assert attempted == {} and capped == {}


@pytest.mark.parametrize("oracle", ORACLES)
def test_every_oracle_is_reachable_and_named(oracle: str) -> None:
    segments = build_segments(SEED)
    parts, _f, _a, _c = injection_partitions(segments, _case("EMAIL"), oracle=oracle)
    assert parts, f"{oracle} enumerated nothing"


# --------------------------------------------------------------------------------------
# Reconstruction and containment: the two properties that make a partition a measurement
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("needle_id", sorted(nr.measurable_ids()))
@pytest.mark.parametrize("encoding", ["plain", "percent"])
def test_pieces_reconstruct_the_value_byte_for_byte(needle_id: str, encoding: str) -> None:
    values = nr.needle_values(SEED)
    rendered = _encode(values[needle_id], encoding)
    for family in PARTITION_FAMILIES:
        for cuts in _family_partitions(rendered, family):
            assert "".join(_partition_pieces(rendered, cuts)) == rendered


@pytest.mark.parametrize("needle_id", sorted(nr.measurable_ids()))
@pytest.mark.parametrize("encoding", ["plain", "percent"])
def test_no_piece_contains_the_complete_needle(needle_id: str, encoding: str) -> None:
    """Otherwise a 'fragmentation' leak is an ordinary undetected whole value."""
    values = nr.needle_values(SEED)
    rendered = _encode(values[needle_id], encoding)
    for family in PARTITION_FAMILIES:
        for cuts in _family_partitions(rendered, family):
            pieces = _partition_pieces(rendered, cuts)
            assert all(rendered not in piece for piece in pieces)
            assert all(piece for piece in pieces), "an empty piece is not a partition"
            assert len(pieces) == len(cuts) + 1


def test_partition_pieces_refuses_a_partition_that_would_not_reconstruct() -> None:
    """The guard is in the producer, not only in this test file."""
    with pytest.raises(RuntimeError):
        _partition_pieces("abcdef", (0,))  # empty first piece
    with pytest.raises(RuntimeError):
        _partition_pieces("abcdef", (3, 3))  # empty middle piece


# --------------------------------------------------------------------------------------
# The resource cap
# --------------------------------------------------------------------------------------


def test_a_capped_family_is_not_run_and_is_not_scored_as_containment() -> None:
    """An aborted combinatorial run must never look like a target that held.

    The cap is set below the three-part count on purpose. The family must come back with
    ZERO attempted partitions and `capped: True` -- not a shortened list, which would
    publish a worst case over a set nobody can name.
    """
    segments = build_segments(SEED)
    case = _case("EMAIL")
    rendered = _encode(segments.injection["EMAIL"], "plain")
    three = math.comb(len(rendered) - 1, 2)
    parts, families, attempted, capped = injection_partitions(
        segments, case, oracle="union-worst-case", cap=three - 1
    )
    assert capped["exhaustive-3-part"] is True
    assert attempted["exhaustive-3-part"] == 0
    assert "exhaustive-3-part" not in set(families)
    assert len(parts) == len(rendered) - 1  # only the two-part family ran


def test_the_default_cap_admits_every_needle_in_the_shipped_corpus() -> None:
    """A cap that silently excluded a corpus needle would make the row incomparable."""
    values = nr.needle_values(SEED)
    for needle_id, value in values.items():
        for encoding in ("plain", "percent"):
            rendered = _encode(value, encoding)
            count = math.comb(len(rendered) - 1, 2)
            assert count <= DEFAULT_PARTITION_CAP, (
                f"{needle_id}/{encoding} needs {count} three-part partitions, over the "
                f"{DEFAULT_PARTITION_CAP} cap: it would publish as inconclusive"
            )


# --------------------------------------------------------------------------------------
# The union statistic
# --------------------------------------------------------------------------------------


def _fake(case: dict[str, str], leaked: bool, attempted: dict, leaked_by: dict) -> RunResult:
    return RunResult(
        policy="p",
        case=case,
        client_text="x",
        echo_recovered={},
        echo_observable=True,
        transport_error=None,
        injection_leaked=leaked,
        events_observed=3,
        upstream_bodies=[],
        latency_ms=[1.0],
        oracle="union-worst-case",
        partitions_attempted=attempted,
        partitions_leaked=leaked_by,
        partitions_capped={f: False for f in attempted},
        split_points_tried=sum(attempted.values()) or 1,
        split_points_leaked=sum(leaked_by.values()),
    )


def test_the_union_leak_rate_is_never_below_either_component() -> None:
    """A union cannot leak in fewer cases than a family it contains.

    Constructed so the two families disagree: family A defeats case 1 only, family B
    defeats case 2 only, so each component is 0.5 and the union is 1.0. If the block ever
    reported a union below a component the arithmetic is wrong, and `never_below_components`
    is what says so in the artefact rather than in a test that may not be run.
    """
    two, three = "exhaustive-2-part", "exhaustive-3-part"
    results = []
    for i, (a_leaks, b_leaks) in enumerate([(1, 0), (0, 1)]):
        base = _case("EMAIL", request_site="chat-content" if i == 0 else "system-content")
        results.append(
            _fake(base, bool(a_leaks or b_leaks),
                  {two: 10, three: 45}, {two: a_leaks, three: b_leaks})
        )
        results.append(_fake(dict(base, fragmentation="single_chunk"), False, {}, {}))
    block = _partition_oracle_block(results)
    assert block["families"][two]["leak_rate_adversarial"] == 0.5
    assert block["families"][three]["leak_rate_adversarial"] == 0.5
    assert block["worst_case"]["leak_rate_adversarial"] == 1.0
    assert block["worst_case"]["never_below_components"] is True
    assert block["worst_case"]["families_in_union"] == [two, three]


def test_the_split_count_decomposition_is_three_distinct_numbers() -> None:
    """`252 splits` was `236 adversarial partitions + 16 uncut requests`.

    The erratum came from summing the per-case attempt count over BOTH arms and calling
    the total splits. This pins all three numbers and the sentence that carries them.
    """
    two = "exhaustive-2-part"
    results = []
    for i in range(4):
        base = _case("EMAIL", request_site=f"site{i}")
        results.append(_fake(base, True, {two: 20}, {two: 3}))
        results.append(_fake(dict(base, fragmentation="single_chunk"), False, {}, {}))
    block = _partition_oracle_block(results)
    assert block["adversarial_partitions"] == 80
    assert block["uncut_single_chunk_requests"] == 4
    assert block["captured_requests_total"] == 84
    sentence = block["method_limit_sentence"]
    assert "80 internal adversarial partitions" in sentence
    assert "4 uncut single-chunk requests" in sentence
    assert "84 captured requests total" in sentence
    assert "not to arbitrary streams" in sentence


def test_a_midpoint_run_publishes_a_null_worst_case_not_a_zero_one() -> None:
    """The number that would mean "not measured" must not be published as 0.0.

    46 of the rows in the published tree are midpoint rows. If `worst_case` filled itself
    in with zeros there, every one of them would carry `worst_case.delta_frag: 0.0` -- a
    quotable number for a statistic that was never computed. This is the same rule
    `coalescing_rate` already follows: it is `null` rather than `0.0` when nothing was
    comparable, because "never observed" and "measured at zero" are different claims and
    zero asserts the second.
    """
    results = [
        _fake(_case("EMAIL"), True, {"midpoint": 1}, {"midpoint": 1}),
        _fake(_case("EMAIL", fragmentation="single_chunk"), False, {}, {}),
    ]
    for result in results:
        object.__setattr__(result, "oracle", "midpoint")
    block = _partition_oracle_block(results)
    assert block["oracle"] == "midpoint"
    assert block["families"] == {}
    worst = block["worst_case"]
    assert worst["families_in_union"] == []
    for field in ("leak_rate_adversarial", "leak_rate_single_chunk_paired", "delta_frag"):
        assert worst[field] is None, f"{field} is {worst[field]!r}, not null"
    assert "no union-based worst-case statistic is defined" in worst["definition"]


def test_the_oracle_label_survives_an_adversarial_arm_that_died_entirely() -> None:
    """A run whose adversarial cases all failed must not be relabelled `midpoint`.

    `run_case` records the oracle on BOTH arms, so the label is available even when no
    adversarial case survived. Reading it only off the survivors made a report contradict
    its own method under total adversarial failure -- the same defect
    `fragmentation_strategy` had when it printed `exhaustive-2-part` about a midpoint cut.
    """
    dead = _fake(_case("EMAIL"), False, {"exhaustive-2-part": 20},
                 {"exhaustive-2-part": 0})
    object.__setattr__(dead, "transport_error", "Timeout")
    alive = _fake(_case("EMAIL", fragmentation="single_chunk"), False, {}, {})
    for result in (dead, alive):
        object.__setattr__(result, "oracle", "exhaustive-2-part")
    block = _partition_oracle_block([dead, alive])
    assert block["oracle"] == "exhaustive-2-part"
    assert _fragmentation_strategy_label([dead, alive]) == "exhaustive-2-part"


def test_a_capped_family_does_not_make_the_union_look_smaller_than_a_component() -> None:
    """The comparison denominator must be the union's, not each family's own.

    Constructed so the two families have DIFFERENT case sets: the three-part family is
    capped on the one case the two-part family caught. Compared on its own denominator the
    two-part component is 1.00 while the union is 0.00, and `never_below_components` would
    read false for a run in which nothing is wrong. On the union's denominator both are
    0.00 and the inequality is the arithmetic check it is meant to be.
    """
    two, three = "exhaustive-2-part", "exhaustive-3-part"
    leaky = _fake(_case("EMAIL", request_site="chat-content"), True,
                  {two: 20, three: 0}, {two: 3, three: 0})
    object.__setattr__(leaky, "partitions_capped", {two: False, three: True})
    quiet = _fake(_case("EMAIL", request_site="system-content"), False,
                  {two: 20, three: 190}, {two: 0, three: 0})
    twins = [
        _fake(_case("EMAIL", request_site=s, fragmentation="single_chunk"), False, {}, {})
        for s in ("chat-content", "system-content")
    ]
    block = _partition_oracle_block([leaky, quiet, *twins])
    assert block["families"][two]["leak_rate_adversarial"] == 0.5
    assert block["families"][three]["cases_capped"] == 1
    worst = block["worst_case"]
    assert worst["cases_excluded_by_cap"] == 1
    assert worst["component_leak_rates_on_union_denominator"] == {two: 0.0, three: 0.0}
    assert worst["never_below_components"] is True


def _published_union_report() -> dict:
    path = (
        Path(__file__).resolve().parents[2]
        / "benchmarks/results/v2-response-split/exhaustive-splits/bounded-retention.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_numeric_audit_rejects_a_union_below_a_union_denominator_component(
    tmp_path: Path,
) -> None:
    """A printed mismatch must enter Audit.failures and make the CLI exit nonzero."""
    report = _published_union_report()
    worst = report["metrics"]["partition_oracle"]["worst_case"]
    family = worst["families_in_union"][0]
    worst["leak_rate_adversarial"] = 0.0
    worst["component_leak_rates_on_union_denominator"][family] = 0.5
    path = tmp_path / "invalid-union.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    audit = numeric_audit.Audit()
    numeric_audit.audit_report(audit, path, tmp_path)
    assert any("union floor" in failure for failure in audit.failures)
    assert numeric_audit.main([
        "--v2", str(tmp_path), "--fide", str(tmp_path / "absent")
    ]) == 1


def test_numeric_audit_accepts_a_capped_unequal_denominator_union(tmp_path: Path) -> None:
    """A native 0.5 family rate does not floor a union whose shared-denominator rate is 0."""
    report = _published_union_report()
    block = report["metrics"]["partition_oracle"]
    worst = block["worst_case"]
    family = worst["families_in_union"][0]
    arm = block["families"][family]
    arm["leak_rate_adversarial"] = 0.5
    arm["delta_frag"] = round(0.5 - arm["leak_rate_single_chunk_paired"], 4)
    worst["leak_rate_adversarial"] = 0.0
    worst["component_leak_rates_on_union_denominator"][family] = 0.0
    worst["cases_excluded_by_cap"] = 1
    path = tmp_path / "valid-capped-union.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    audit = numeric_audit.Audit()
    numeric_audit.audit_report(audit, path, tmp_path)
    assert not audit.failures


def test_the_worst_case_definition_travels_with_the_number() -> None:
    """A 'worst case' with no stated bound is the claim this profile must not make."""
    two = "exhaustive-2-part"
    results = [
        _fake(_case("EMAIL"), True, {two: 10}, {two: 1}),
        _fake(_case("EMAIL", fragmentation="single_chunk"), False, {}, {}),
    ]
    block = _partition_oracle_block(results)
    definition = block["worst_case"]["definition"]
    assert "NOT a worst case over arbitrary streams" in definition
    assert "measured corpus values" in definition


# --------------------------------------------------------------------------------------
# Pairing and the discordance table
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "axes,array",
    [(AXES, covering_array()), (fide.FIDE_AXES, fide.fide_covering_array())],
    ids=["v2", "fide"],
)
def test_every_fragmentation_twin_matches_on_every_other_axis(axes, array) -> None:
    """DeltaFrag is a difference of marginal rates; the twins are what license reading it
    as a fragmentation effect rather than a composition difference."""
    single = {_twin_key(c) for c in array if c["fragmentation"] == "single_chunk"}
    adversarial = {_twin_key(c) for c in array if c["fragmentation"] == "adversarial"}
    assert single == adversarial, "an unpaired case makes the two arms different populations"
    assert len(single) == len(array) // 2
    for case in array:
        key = dict(_twin_key(case))
        assert set(key) == set(axes) - {"fragmentation"}


def test_the_discordance_table_reproduces_delta_frag_and_keeps_the_direction() -> None:
    """Equal disagreement in both directions and no disagreement both give DeltaFrag 0."""
    def pair(site, single_leak, adv_leak):
        base = _case("EMAIL", request_site=site)
        return [
            _fake(dict(base, fragmentation="single_chunk"), single_leak, {}, {}),
            _fake(base, adv_leak, {"exhaustive-2-part": 5}, {"exhaustive-2-part": 1}),
        ]

    balanced = pair("a", False, True) + pair("b", True, False)
    table = _discordance(balanced)
    assert table["adversarial_only"] == 1 and table["single_chunk_only"] == 1
    assert table["delta_frag_from_discordance"] == 0.0

    quiet = pair("a", False, False) + pair("b", True, True)
    table2 = _discordance(quiet)
    assert table2["adversarial_only"] == 0 and table2["single_chunk_only"] == 0
    assert table2["delta_frag_from_discordance"] == 0.0
    assert table != table2, (
        "two runs with the same DeltaFrag and completely different pairwise evidence "
        "must not produce the same table"
    )


def test_a_half_dead_pair_is_dropped_from_the_table_not_counted_as_concordant() -> None:
    base = _case("EMAIL")
    twin = dict(base, fragmentation="single_chunk")
    dead = _fake(twin, False, {}, {})
    object.__setattr__(dead, "transport_error", "Timeout")
    table = _discordance([_fake(base, True, {"exhaustive-2-part": 5}, {"exhaustive-2-part": 1}), dead])
    assert table["pairs_complete"] == 0
    assert table["pairs_incomplete"] == 1
    assert table["both_arms_leaked"] == table["adversarial_only"] == 0


# --------------------------------------------------------------------------------------
# The FIDE axis
# --------------------------------------------------------------------------------------


def test_needle_class_is_a_corpus_axis_and_reaches_the_case_digest() -> None:
    """If the class is not in the case definitions it is not in `corpus.sha256`, and two
    corpora differing only by class would be indistinguishable."""
    array = fide.fide_covering_array()
    assert "needle_class" in fide.FIDE_AXES
    assert all("needle_class" in case for case in array)

    import hashlib
    import json

    def digest(axes) -> str:
        defs = sorted(
            ({k: c[k] for k in sorted(axes)} for c in array),
            key=lambda c: tuple(sorted(c.items())),
        )
        return hashlib.sha256(
            json.dumps(defs, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    without = {k: v for k, v in fide.FIDE_AXES.items() if k != "needle_class"}
    assert digest(fide.FIDE_AXES) != digest(without)


def test_needle_class_participates_in_pairwise_coverage() -> None:
    array = fide.fide_covering_array()
    required = _all_pairs(fide.FIDE_AXES, fide.feasible)
    covered: set = set()
    for case in array:
        covered |= _pairs_of(case, fide.FIDE_AXES)
    assert required <= covered, "the FIDE array does not prove its own coverage"
    assert any(a == "needle_class" or b == "needle_class" for a, _va, b, _vb in required)


def test_impossible_entity_class_pairs_are_not_required() -> None:
    """A needle belongs to one class, so `(AWSKEYID, pii)` is impossible, not uncovered.

    Requiring it would make `proof_complete` unsatisfiable and the schema's coverage gate
    -- which forbids a pass on incomplete coverage -- permanently unreachable.
    """
    required = _all_pairs(fide.FIDE_AXES, fide.feasible)
    for needle in nr.NEEDLES:
        if needle.needle_class == "prompt_injection":
            continue
        for cls in fide.FIDE_AXES["needle_class"]:
            pair = ("entity", needle.id, "needle_class", cls)
            assert (pair in required) == (cls == needle.needle_class)


def test_both_classes_are_equally_represented_in_both_arms() -> None:
    """An unbalanced class split would make the pooled DeltaFrag a mixture whose weights
    nobody published."""
    from collections import Counter

    array = fide.fide_covering_array()
    counts = Counter((c["needle_class"], c["fragmentation"]) for c in array)
    assert len(set(counts.values())) == 1, counts


def test_shipping_gateway_has_its_own_label_and_claim_scoped_detectors() -> None:
    """A product row must not inherit a study control's name or detector claims."""
    target = fide.FIDE_EXTERNAL_TARGETS["llm-shield-proxy-1.6.0-response-on"]
    assert "SLACKBOT" not in target["enabled"]
    assert {"AWSKEYID", "GHTOKEN", "PEMBLOCK"} <= set(target["enabled"])
    assert target["version"] == "1.6.0"
    assert "LLM-Shield-Proxy 1.6.0" in target["claim_citation"]


def test_claim_scoped_metrics_exclude_not_applicable_needles() -> None:
    """An unsupported Slack shape must not become a product detector miss."""
    report = {
        "entity_scope": {
            "enabled": ["AWSKEYID", "EMAIL"],
            "not_enabled": ["SLACKBOT"],
        },
        "metrics": {
            "by_axis_arm": {
                "entity": {
                    "AWSKEYID": {
                        "single_chunk": {"applicable": 2, "leaked": 0},
                        "adversarial": {"applicable": 2, "leaked": 2},
                    },
                    "EMAIL": {
                        "single_chunk": {"applicable": 2, "leaked": 0},
                        "adversarial": {"applicable": 2, "leaked": 1},
                    },
                    "SLACKBOT": {
                        "single_chunk": {"applicable": 2, "leaked": 2},
                        "adversarial": {"applicable": 2, "leaked": 2},
                    },
                }
            }
        },
    }
    scoped = claim_scoped_metrics(report)
    assert scoped["not_enabled_entities"] == ["SLACKBOT"]
    assert scoped["overall"]["single_chunk"] == {
        "applicable": 4,
        "leaked": 0,
        "leak_rate": 0.0,
    }
    assert scoped["overall"]["adversarial"]["leak_rate"] == 0.75
    assert scoped["overall"]["delta_frag"] == 0.75
    assert scoped["by_class"]["secret"]["adversarial"]["applicable"] == 2


def test_fide_promotion_keeps_reports_and_aggregate_but_not_sidecars(tmp_path: Path) -> None:
    (tmp_path / "midpoint" / "seed").mkdir(parents=True)
    report = tmp_path / "midpoint" / "seed" / "row.json"
    sidecar = tmp_path / "midpoint" / "seed" / "row.summary.json"
    aggregate = tmp_path / "fide-sweep.json"
    for path in (report, sidecar, aggregate):
        path.write_text("{}", encoding="utf-8")
    assert [p.relative_to(tmp_path).as_posix() for p in publishable_fide_files(tmp_path)] == [
        "fide-sweep.json",
        "midpoint/seed/row.json",
    ]


def test_incremental_fide_promotion_never_copies_a_partial_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = tmp_path / "staging"
    published = tmp_path / "published"
    staged_report = staging / "new-oracle" / "new.json"
    old_report = published / "old-oracle" / "old.json"
    staged_report.parent.mkdir(parents=True)
    old_report.parent.mkdir(parents=True)
    staged_report.write_text("{}", encoding="utf-8")
    old_report.write_text("{}", encoding="utf-8")
    (staging / "fide-sweep.json").write_text('{"partial": true}', encoding="utf-8")
    published_aggregate = published / "fide-sweep.json"
    published_aggregate.write_text('{"complete": true}', encoding="utf-8")

    rebuilt: list[Path] = []
    monkeypatch.setattr(fide_sweep, "STAGING", staging)
    monkeypatch.setattr(fide_sweep, "PUBLISHED", published)
    monkeypatch.setattr(fide_sweep, "summarise", lambda root: rebuilt.append(root) or 0)

    assert fide_sweep.stage_promote() == 0
    assert (published / "new-oracle" / "new.json").is_file()
    assert old_report.is_file()
    assert published_aggregate.read_text(encoding="utf-8") == '{"complete": true}'
    assert rebuilt == [published]


def test_llm_guard_promotion_keeps_only_raw_reports(tmp_path: Path) -> None:
    (tmp_path / "seed").mkdir(parents=True)
    report = tmp_path / "seed" / "row.json"
    sidecar = tmp_path / "seed" / "row.summary.json"
    report.write_text("{}", encoding="utf-8")
    sidecar.write_text("{}", encoding="utf-8")
    assert [
        p.relative_to(tmp_path).as_posix()
        for p in publishable_llm_guard_files(tmp_path)
    ] == ["seed/row.json"]


# --------------------------------------------------------------------------------------
# Baseline blindness is not fragmentation evasion
# --------------------------------------------------------------------------------------


def test_baseline_blindness_and_fragmentation_evasion_are_different_results() -> None:
    """A target that misses the WHOLE value has not been defeated by a boundary.

    `detector_blind_entities` is derived from the single-chunk arm only. A blind entity
    contributes leaks to both arms and therefore nothing to DeltaFrag, and reporting the
    two together would blame chunk boundaries for a value the detector never finds.
    """
    from pii_leak_benchmark.v2_emitter import build_report, check_segment_separation

    segments = build_segments(SEED)
    results = []
    for entity in AXES["entity"]:
        for site in AXES["request_site"]:
            base = _case(entity, request_site=site)
            # EMAIL is blind: it leaks in BOTH arms. SSN leaks only when fragmented.
            blind = entity == "EMAIL"
            adv_leak = blind or entity == "SSN"
            results.append(_fake(dict(base, fragmentation="single_chunk"), blind, {}, {}))
            results.append(_fake(base, adv_leak, {"exhaustive-2-part": 5},
                                 {"exhaustive-2-part": 1 if adv_leak else 0}))
    separation = check_segment_separation(segments, "{}")
    report = build_report(segments, results, separation, SEED)
    metrics = report["metrics"]
    assert metrics["detector_blind_entities"] == ["EMAIL"]
    arms = metrics["by_axis_arm"]["entity"]
    assert arms["EMAIL"]["delta_frag"] == 0.0, "a blind entity contributes no fragmentation effect"
    assert arms["SSN"]["delta_frag"] == 1.0
    assert arms["EMAIL"]["single_chunk"]["leak_rate"] == 1.0


def test_negative_delta_frag_is_representable_and_carries_its_baseline() -> None:
    """A fragment can match for an unrelated reason and suppress a real leak.

    Measured on seed 0000000000000001: the complete US phone number was missed and the
    fragment `5-0126` was redacted, so the fragmented arm scored SAFER. The emitter must
    be able to represent that, and the baseline must be published beside it -- a negative
    difference read alone says "fragmentation is safe here", which is the opposite of
    what happened.
    """
    from pii_leak_benchmark.v2_emitter import build_report, check_segment_separation

    segments = build_segments(SEED)
    results = []
    for entity in AXES["entity"]:
        for site in AXES["request_site"]:
            base = _case(entity, request_site=site)
            single_leak = entity == "USPHONE"
            results.append(
                _fake(dict(base, fragmentation="single_chunk"), single_leak, {}, {})
            )
            results.append(_fake(base, False, {"exhaustive-2-part": 5},
                                 {"exhaustive-2-part": 0}))
    report = build_report(
        segments, results, check_segment_separation(segments, "{}"), SEED
    )
    metrics = report["metrics"]
    assert metrics["delta_frag"] < 0
    assert metrics["by_axis_arm"]["entity"]["USPHONE"]["delta_frag"] == -1.0
    # The baseline that makes it readable is in the same block, not in prose elsewhere.
    assert metrics["leak_rate"]["single_chunk"] == 0.25
    assert metrics["discordance"]["single_chunk_only"] == 4
    assert metrics["discordance"]["adversarial_only"] == 0
