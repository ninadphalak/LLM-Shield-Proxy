"""Every published results directory, checked on its own terms — including the new one.

`test_results_are_comparable.py` guards `benchmarks/results/v2-response-split`, the PII
baseline, and its assertions are written in terms of that directory. `spec/v2.1.0` (FIDE)
adds a `needle_class` axis, so its rows are a DIFFERENT CORPUS GENERATION and live in a
different directory. They need every one of the same guarantees, on their own terms.

This file parameterises those guarantees over the profile registry below rather than
copying them, and it adds three the older file does not have: schema validation against
each profile's own schema, all-inconclusive rejection, and agreement between the three
places in a report that describe what the oracle did.

WHAT IS NEVER ASSERTED HERE: that one profile's numbers are comparable with another's.
A six-axis FIDE row and a five-axis v2 row measure different case sets and cell-for-cell
comparison between them is exactly the failure this project has already had once, when
four corpus generations sat in one directory and the numbers were "individually correct
and jointly misleading".

WHAT IS ASSERTED ACROSS PROFILES: the INSTRUMENT. Both directories must carry the same
`inspector_sha256`, because the article's generality argument puts a PII result and a
secret result in one sentence, and that sentence is only legitimate if the same code
scored both. If the two profiles ever drift onto different inspectors, the comparison
stops being a comparison and nothing in the corpus metadata would say so.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pii-leak-benchmark"))

from pii_leak_benchmark.v2_emitter import instrument_block  # noqa: E402

PROFILES: dict[str, Path] = {
    "v2-response-split": ROOT / "benchmarks" / "results" / "v2-response-split",
    "fide-v2.1": ROOT / "benchmarks" / "results" / "fide-v2.1",
}

SCHEMAS: dict[str, Path] = {
    "v2-response-split": ROOT / "spec" / "v2.0.0" / "http-profile.schema.json",
    "fide-v2.1": ROOT / "spec" / "v2.1.0" / "http-profile.schema.json",
}

EXPECTED_CORPUS_ID: dict[str, str] = {
    "v2-response-split": "minimal-response-split",
    "fide-v2.1": "fide-response-split",
}


def artefacts(root: Path) -> list[Path]:
    """Single-run reports under one profile root, RECURSIVELY.

    Recursive because both trees have nested directories -- `exhaustive-splits/`,
    `exhaustive-presidio-seed-sweep/<seed>/`, and the FIDE oracle directories -- and a
    non-recursive glob left nine published rows, four of them with no instrument block at
    all, covered by no test in this repository for two days.
    """
    if not root.is_dir():
        return []
    return sorted(
        p
        for p in root.rglob("*.json")
        if not p.name.startswith("seed-sweep")
        and p.name != "fide-sweep.json"
        and not p.name.endswith(".summary.json")
    )


def _load(root: Path) -> dict[Path, dict]:
    return {p: json.loads(p.read_text(encoding="utf-8")) for p in artefacts(root)}


def test_derived_aggregates_are_not_selected_as_single_run_reports(tmp_path: Path) -> None:
    """Aggregate JSON has a different schema and must not enter report provenance tests."""
    (tmp_path / "fide-sweep.json").write_text("{}", encoding="utf-8")
    (tmp_path / "real-report.json").write_text("{}", encoding="utf-8")
    (tmp_path / "real-report.summary.json").write_text("{}", encoding="utf-8")
    assert [p.name for p in artefacts(tmp_path)] == ["real-report.json"]


def _skip_if_empty(profile: str) -> dict[Path, dict]:
    reports = _load(PROFILES[profile])
    if not reports:
        pytest.skip(f"{profile}: no artefacts published yet")
    return reports


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_each_profile_is_internally_comparable(profile: str) -> None:
    reports = _skip_if_empty(profile)
    root = PROFILES[profile]

    def rel(path: Path) -> str:
        return path.relative_to(root).as_posix()

    counts: dict[int, list[str]] = {}
    for path, report in reports.items():
        counts.setdefault(report["corpus"]["case_count"], []).append(rel(path))
    assert len(counts) == 1, (
        f"{profile}: artefacts from different corpus generations are sitting in one "
        "directory and will be read as comparable:\n"
        + "\n".join(
            f"  {n} cases: {', '.join(sorted(names))}" for n, names in sorted(counts.items())
        )
    )

    axes = {tuple(sorted(r["corpus"]["coverage"]["axes"])) for r in reports.values()}
    assert len(axes) == 1, f"{profile}: rows measured over different axis sets: {axes}"

    digests = {r["corpus"]["sha256"] for r in reports.values()}
    assert len(digests) == 1, (
        f"{profile}: {len(digests)} distinct corpus digests; the rows are not measuring "
        "the same case definitions whatever their case counts say"
    )

    ids = {r["corpus"]["id"] for r in reports.values()}
    assert ids == {EXPECTED_CORPUS_ID[profile]}, (
        f"{profile}: expected corpus id {EXPECTED_CORPUS_ID[profile]!r}, found {ids}. "
        "A row filed under the wrong profile is a row nothing is guarding."
    )

    incomplete = [
        rel(p) for p, r in reports.items() if not r["corpus"]["coverage"]["proof_complete"]
    ]
    assert not incomplete, f"{profile}: coverage proof incomplete: {incomplete}"


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_each_profile_was_scored_by_the_current_instrument(profile: str) -> None:
    reports = _skip_if_empty(profile)
    expected = instrument_block()
    stale = []
    for path, report in reports.items():
        block = report.get("instrument")
        if block is None:
            stale.append(f"{path.name} (no instrument block)")
        elif block != expected:
            differing = sorted(k for k, v in expected.items() if block.get(k) != v)
            stale.append(f"{path.name} (differs at {', '.join(differing)})")
    assert not stale, (
        f"{profile}: rows produced by an instrument that is not the current one, so "
        "their numbers are not comparable with a fresh run:\n  "
        + "\n  ".join(stale)
        + f"\ncurrent instrument: {expected}\n"
        "Re-run them, or move them out of the published directory. Do not weaken this "
        "test: a stale row is worse than a missing one, because a missing row is visibly "
        "absent and a stale one looks like evidence."
    )


def test_the_two_profiles_share_one_inspector() -> None:
    """The identity the generality argument rests on.

    The article says FIDE is not intrinsically a PII failure and supports it by putting a
    PII DeltaFrag and a secret DeltaFrag side by side. That sentence is only legitimate if
    the same scoring code produced both numbers.
    """
    seen: dict[str, set[str]] = {}
    for profile, root in PROFILES.items():
        digests = {
            r.get("instrument", {}).get("inspector_sha256")
            for r in _load(root).values()
        }
        if digests:
            seen[profile] = digests
    if len(seen) < 2:
        pytest.skip("only one profile is published")
    union = set().union(*seen.values())
    assert len(union) == 1, (
        "the published profiles were scored by different inspectors, so a cross-class "
        f"comparison between them is not licensed: {seen}"
    )


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_every_published_row_validates_against_its_own_profile_schema(profile: str) -> None:
    """A FIDE row validating against the v2 schema would mean the sixth axis is optional."""
    jsonschema = pytest.importorskip("jsonschema")
    reports = _skip_if_empty(profile)
    schema_path = SCHEMAS[profile]
    assert schema_path.is_file(), f"{profile}: no schema at {schema_path}"
    validator = jsonschema.Draft202012Validator(
        json.loads(schema_path.read_text(encoding="utf-8"))
    )
    failures = []
    for path, report in reports.items():
        for error in list(validator.iter_errors(report))[:3]:
            failures.append(f"{path.name}: {list(error.path)}: {error.message}")
    assert not failures, f"{profile}: schema-invalid rows:\n  " + "\n  ".join(failures)


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_no_published_row_is_entirely_inconclusive(profile: str) -> None:
    """A run in which every case died reports four rates of 0.00 and reads as perfect.

    Not hypothetical: a container that failed to start produced exactly such a row, and it
    looked like the best result in the table.
    """
    reports = _skip_if_empty(profile)
    bad = []
    for path, report in reports.items():
        metrics = report["metrics"]
        applicable = metrics["cases_applicable"]
        attempted = metrics["cases_scored"]
        inconclusive = metrics["cases_inconclusive"]
        if applicable == 0 or inconclusive >= attempted:
            bad.append(
                f"{path.name}: {applicable} applicable of {attempted} attempted, "
                f"{inconclusive} inconclusive"
            )
    assert not bad, f"{profile}: all-inconclusive rows published:\n  " + "\n  ".join(bad)


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_the_three_descriptions_of_the_oracle_agree(profile: str) -> None:
    """The `252 splits` erratum, closed at the guard rather than at the string.

    Three places in a report say what the oracle did: `metrics.partition_oracle`, the
    `fragmentation_strategy` enum, and the `method_limits` sentence. They were allowed to
    disagree and they did -- for every exhaustive row in the published tree, across two
    evidence tags. Any two of them disagreeing is now a failure.
    """
    reports = _skip_if_empty(profile)
    problems = []
    label_for = {
        "midpoint": "across-sse-events",
        "exhaustive-2-part": "exhaustive-2-part",
        "exhaustive-3-part": "exhaustive-3-part",
        "union-worst-case": "union-worst-case",
    }
    for path, report in reports.items():
        block = report["metrics"].get("partition_oracle")
        if block is None:
            problems.append(f"{path.name}: no partition_oracle block")
            continue
        label = report["checks"]["response_injection_containment"]["fragmentation_strategy"]
        if label != label_for[block["oracle"]]:
            problems.append(f"{path.name}: oracle {block['oracle']} but label {label}")

        total = block["adversarial_partitions"] + block["uncut_single_chunk_requests"]
        if total != block["captured_requests_total"]:
            problems.append(
                f"{path.name}: {block['adversarial_partitions']} adversarial partitions + "
                f"{block['uncut_single_chunk_requests']} uncut != "
                f"{block['captured_requests_total']} captured"
            )

        sentence = block["method_limit_sentence"]
        if sentence not in report["limitations"]["method_limits"]:
            problems.append(
                f"{path.name}: method_limits does not carry the oracle's own sentence"
            )
        if str(block["captured_requests_total"]) in sentence:
            if "captured requests total" not in sentence:
                problems.append(
                    f"{path.name}: the request total is printed without the label that "
                    "distinguishes it from the split count -- this is the 252 erratum"
                )

        worst = block["worst_case"]
        if worst["families_in_union"]:
            # Components are compared on the UNION's denominator; see the schema note.
            for family, component in worst[
                "component_leak_rates_on_union_denominator"
            ].items():
                if worst["leak_rate_adversarial"] < component:
                    problems.append(
                        f"{path.name}: union leak rate {worst['leak_rate_adversarial']} "
                        f"is below its component {family} at {component}"
                    )
            if not worst.get("never_below_components"):
                problems.append(f"{path.name}: never_below_components is not true")
        else:
            # NO FAMILY ENUMERATED, so the statistic does not exist for this row. It must
            # be null, not zero: a midpoint row publishing worst_case.delta_frag = 0.0
            # would hand a reader a number that means "not measured".
            for field in ("leak_rate_adversarial", "leak_rate_single_chunk_paired",
                          "delta_frag"):
                if worst[field] is not None:
                    problems.append(
                        f"{path.name}: no family was enumerated but worst_case.{field} "
                        f"is {worst[field]!r} rather than null"
                    )
        if "arbitrary streams" not in worst["definition"]:
            problems.append(
                f"{path.name}: the worst-case definition does not carry its own bound"
            )
    assert not problems, f"{profile}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_the_discordance_table_reproduces_the_published_delta_frag(profile: str) -> None:
    """DeltaFrag is a paired statistic and the table is what makes that checkable.

    The headline is the difference of the two stored four-decimal marginal rates.  The
    table's direct discordant-count quotient can differ by one final decimal unit at a
    half-even boundary, so validate both legitimate rounding routes explicitly.
    """
    reports = _skip_if_empty(profile)
    problems = []
    for path, report in reports.items():
        metrics = report["metrics"]
        table = metrics.get("discordance")
        if table is None:
            problems.append(f"{path.name}: no discordance block")
            continue
        if table["pairs_incomplete"]:
            # A dropped twin legitimately makes the marginal difference and the paired
            # table disagree. Recording that is the point; it is not a failure.
            continue
        pairs = table["pairs_complete"]
        paired_adversarial = round(
            (table["both_arms_leaked"] + table["adversarial_only"]) / pairs, 4
        )
        paired_single = round(
            (table["both_arms_leaked"] + table["single_chunk_only"]) / pairs, 4
        )
        reconstructed = round(paired_adversarial - paired_single, 4)
        if reconstructed != metrics["delta_frag"]:
            problems.append(
                f"{path.name}: delta_frag {metrics['delta_frag']} but the paired table "
                f"reconstructs the stored-rate difference as {reconstructed}"
            )
        direct = round(
            (table["adversarial_only"] - table["single_chunk_only"]) / pairs, 4
        )
        if table["delta_frag_from_discordance"] != direct:
            problems.append(
                f"{path.name}: stored discordance delta "
                f"{table['delta_frag_from_discordance']} but counts give {direct}"
            )
        counted = (
            table["both_arms_leaked"]
            + table["adversarial_only"]
            + table["single_chunk_only"]
            + table["neither_arm_leaked"]
        )
        if counted != table["pairs_complete"]:
            problems.append(
                f"{path.name}: table cells sum to {counted}, pairs_complete is "
                f"{table['pairs_complete']}"
            )
    assert not problems, f"{profile}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_the_fide_profile_publishes_its_needle_provenance(profile: str) -> None:
    """`corpus.seed` pins generated values and says nothing about fixed literals.

    Every secret needle is a fixed literal, so without the registry digest a fixture edit
    is invisible in the artefact: same seed, same corpus digest, different value.
    """
    if profile != "fide-v2.1":
        pytest.skip("FIDE-specific provenance")
    reports = _skip_if_empty(profile)
    from pii_leak_benchmark import needle_registry as nr

    expected = nr.registry_digest()
    stale = [
        path.name
        for path, report in reports.items()
        if report["corpus"].get("needle_registry_sha256") != expected
    ]
    assert not stale, (
        "rows whose needle registry is not the current one:\n  " + "\n  ".join(stale)
    )
    for path, report in reports.items():
        assert set(report["corpus"]["needle_classes"]) == set(nr.classes_in_use()), path.name
        assert "prompt_injection" in report["corpus"]["needle_classes_declared_unmeasured"], (
            f"{path.name}: an unmeasured class must be published as unmeasured, not omitted"
        )
