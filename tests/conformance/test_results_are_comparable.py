"""Every published v2 artefact must come from the same corpus.

Rows in `benchmarks/results/v2-response-split/` are read side by side, in the README and
in the paper drafted from it. If two of them were produced by different corpus
generations, comparing them is meaningless -- and nothing in a report's headline numbers
says which generation produced it.

This is not hypothetical. On 2026-09-04 that directory briefly held artefacts from FOUR
generations at once: 6 cases over 4 axes, 12 and 24 over 5 axes, and 32 over 5 axes,
because the corpus was extended three times and only the rows being worked on were
re-run. The numbers were individually correct and jointly misleading, which is the exact
failure this project keeps writing about. A stale row is worse than a missing one: a
missing row is visibly absent, a stale one looks like evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

RESULTS = Path(__file__).resolve().parents[2] / "benchmarks" / "results" / "v2-response-split"


def _artefacts() -> list[Path]:
    """Single-run reports only. Sweep files aggregate many runs and have another shape."""
    return sorted(p for p in RESULTS.glob("*.json") if not p.name.startswith("seed-sweep"))


def _corpus(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["corpus"]


@pytest.mark.skipif(not RESULTS.is_dir(), reason="results directory not present")
def test_every_artefact_used_the_same_case_count() -> None:
    by_count: dict[int, list[str]] = {}
    for path in _artefacts():
        by_count.setdefault(_corpus(path)["case_count"], []).append(path.name)

    assert len(by_count) <= 1, (
        "artefacts from different corpus generations are sitting in one directory and "
        "will be read as comparable:\n"
        + "\n".join(f"  {count} cases: {', '.join(sorted(names))}" for count, names in sorted(by_count.items()))
        + "\nRe-run the stale rows, or delete them. Do not publish them side by side."
    )


@pytest.mark.skipif(not RESULTS.is_dir(), reason="results directory not present")
def test_every_artefact_used_the_same_axes() -> None:
    by_axes: dict[tuple, list[str]] = {}
    for path in _artefacts():
        by_axes.setdefault(tuple(sorted(_corpus(path)["coverage"]["axes"])), []).append(path.name)

    assert len(by_axes) <= 1, (
        "artefacts measured over different axis sets:\n"
        + "\n".join(f"  {list(axes)}: {', '.join(sorted(names))}" for axes, names in by_axes.items())
    )


@pytest.mark.skipif(not RESULTS.is_dir(), reason="results directory not present")
def test_every_artefact_proved_its_coverage() -> None:
    """A row whose pairwise proof is incomplete cannot be compared with one whose is."""
    incomplete = [
        path.name for path in _artefacts() if not _corpus(path)["coverage"]["proof_complete"]
    ]
    assert not incomplete, f"coverage proof incomplete: {incomplete}"


@pytest.mark.skipif(not RESULTS.is_dir(), reason="results directory not present")
def test_the_corpus_digest_is_identical_across_artefacts() -> None:
    """`corpus.sha256` pins the case DEFINITIONS. Two rows with different digests were
    measured against different case sets whatever their case counts say."""
    digests = {_corpus(path)["sha256"] for path in _artefacts()}
    assert len(digests) <= 1, (
        f"{len(digests)} distinct corpus digests across artefacts; the rows are not "
        "measuring the same case definitions"
    )


# --------------------------------------------------------------------------------------
# Same corpus is not enough. Same INSTRUMENT.
#
# The corpus guards above catch a row measured against different case definitions. They
# do not catch a row measured by a different INSPECTOR, and that is the failure that
# actually happened: the leak inspector was fixed twice while every artefact in this
# directory kept its old numbers and its old `inspection_scope` sentence, so a row
# produced by a walk with six blind spots sat next to one produced by a walk without
# them, and nothing said which was which.
#
# A row is stale the moment the thing that scored it changes. `inspection_scope` is the
# right anchor because it is GENERATED from the capability registry, so it changes
# exactly when the inspector's declared reach does -- no version number to remember to
# bump.
# --------------------------------------------------------------------------------------

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pii-leak-benchmark"))

from pii_leak_benchmark.v2_emitter import (  # noqa: E402
    BOUNDARY_INSPECTION_SCOPE,
    CLIENT_INSPECTION_SCOPE,
    instrument_block,
)

_SCOPES = {
    ("checks", "response_injection_containment", "inspection_scope"): CLIENT_INSPECTION_SCOPE,
    ("checks", "configured_upstream_boundary", "inspection_scope"): BOUNDARY_INSPECTION_SCOPE,
}


@pytest.mark.skipif(not RESULTS.is_dir(), reason="results directory not present")
@pytest.mark.parametrize("path_in_report", sorted(_SCOPES))
def test_every_artefact_was_scored_by_the_current_inspector(path_in_report: tuple) -> None:
    expected = _SCOPES[path_in_report]
    stale: list[str] = []
    for path in _artefacts():
        report = json.loads(path.read_text(encoding="utf-8"))
        node = report
        for key in path_in_report:
            node = node.get(key, {}) if isinstance(node, dict) else {}
        if node != expected:
            stale.append(path.name)

    assert not stale, (
        "these rows were produced by an inspector that no longer exists, and their "
        f"numbers are not comparable with a current run:\n  {', '.join(sorted(stale))}\n"
        f"(stale at {'.'.join(path_in_report)})\n"
        "Re-run them, or move them out of the published directory. A stale row is worse "
        "than a missing one: a missing row is visibly absent, a stale one looks like "
        "evidence."
    )


def _sweeps() -> list[Path]:
    return sorted(RESULTS.glob("seed-sweep*.json"))


@pytest.mark.skipif(not RESULTS.is_dir(), reason="results directory not present")
def test_every_sweep_records_which_instrument_produced_it() -> None:
    """The sweeps are what the README says to cite, and they had no provenance at all.

    Single-run artefacts carry `inspection_scope` and the test above guards it. The sweep
    files carried four rates and a seed list, so an inspector change left them looking
    exactly as authoritative as before. `instrument` closes that; a sweep without one
    predates the fix and its numbers are not comparable with a current run.
    """
    import hashlib
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pii-leak-benchmark"))
    from pii_leak_benchmark.v2_emitter import (
        BOUNDARY_INSPECTION_SCOPE,
        CLIENT_INSPECTION_SCOPE,
    )

    def digest(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    expected = {
        "client_scope_sha256": digest(CLIENT_INSPECTION_SCOPE),
        "boundary_scope_sha256": digest(BOUNDARY_INSPECTION_SCOPE),
    }

    stale: list[str] = []
    for path in _sweeps():
        document = json.loads(path.read_text(encoding="utf-8"))
        for policy, block in document.items():
            instrument = block.get("instrument") if isinstance(block, dict) else None
            if instrument is None:
                stale.append(f"{path.name}:{policy} (no instrument block)")
            elif any(instrument.get(k) != v for k, v in expected.items()):
                stale.append(f"{path.name}:{policy} (different inspector)")

    assert not stale, (
        "sweep rows produced by an inspector that no longer exists:\n  "
        + "\n  ".join(stale)
        + "\nThese are the numbers the README tells readers to cite. Re-run them."
    )


# --------------------------------------------------------------------------------------
# `inspection_scope` is the wrong anchor on its own, and the comment above overstated it.
#
# Both scope strings are generated from the CAPABILITY REGISTRIES, so they move when the
# instrument's declared REACH changes. They do not move when its BEHAVIOUR changes, and
# behaviour is what produces the numbers. Three demonstrations, all with this file green:
#
#   1. Re-pointing `_fidelity_check` from `results` to `observable` flipped a published
#      row's `response_fidelity.passed` from false to true. 411 tests passed either way.
#   2. Ten of the nineteen artefacts in this directory were emitted by a build that did
#      not yet have `redaction_claim.request_path_redaction_configured`, so the directory
#      held two emitter builds side by side. Nothing here noticed.
#   3. Deleting the residue scan -- a false pass -- left both scope strings identical.
#
# `instrument.inspector_sha256` digests the source of every function that decides a
# number, normalised through the AST so comments and formatting do not move it. It
# over-invalidates rather than under-invalidating, which is the safe direction here.
# --------------------------------------------------------------------------------------


@pytest.mark.skipif(not RESULTS.is_dir(), reason="results directory not present")
def test_every_artefact_records_the_instrument_that_produced_it() -> None:
    expected = instrument_block()
    stale: list[str] = []
    for path in _artefacts():
        block = json.loads(path.read_text(encoding="utf-8")).get("instrument")
        if block is None:
            stale.append(f"{path.name} (no instrument block)")
        elif block != expected:
            differing = sorted(k for k, v in expected.items() if block.get(k) != v)
            stale.append(f"{path.name} (differs at {', '.join(differing)})")

    assert not stale, (
        "these rows were produced by an instrument that is not the current one, so "
        "their numbers are not comparable with a fresh run:\n  "
        + "\n  ".join(stale)
        + f"\ncurrent instrument: {expected}\n"
        "Re-run them, or move them out of the published directory. Do not weaken "
        "this test: a stale row is worse than a missing one, because a missing row "
        "is visibly absent and a stale one looks like evidence."
    )


def _rate_with_a_flipped_comparison(flags):
    """Behaviourally different from `v2_emitter._rate`, and it has real source.

    Defined at module level on purpose: `inspect.getsource` cannot read a function built
    by `exec`, and the digest is computed from source.
    """
    values = list(flags)
    return round(sum(1 for f in values if not f) / len(values), 4) if values else 0.0


def test_the_instrument_digest_moves_when_behaviour_moves() -> None:
    """The guard above is only worth having if its anchor actually tracks behaviour.

    `inspection_scope` did not, which is how a row scored by a walk with six blind spots
    came to sit next to one scored without them.
    """
    from pii_leak_benchmark import v2_emitter

    before = v2_emitter.inspector_digest()

    # A docstring rewrite must NOT move it, or every prose edit marks the whole directory
    # stale and the guard gets switched off for being noisy.
    original_doc = v2_emitter._present.__doc__
    v2_emitter._present.__doc__ = "rewritten prose, identical behaviour"
    try:
        assert v2_emitter.inspector_digest() == before, (
            "the digest moved on a docstring edit. It will mark every row stale on a "
            "comment change, and a guard that cries wolf gets disabled."
        )
    finally:
        v2_emitter._present.__doc__ = original_doc

    # A one-token behaviour change in a function that decides a rate MUST move it.
    original = v2_emitter._rate
    v2_emitter._rate = _rate_with_a_flipped_comparison
    try:
        assert v2_emitter.inspector_digest() != before, (
            "the instrument digest did not change when a function that decides a "
            "published rate changed. It is not tracking behaviour, which is the whole "
            "reason it exists next to inspection_scope."
        )
    finally:
        v2_emitter._rate = original
    assert v2_emitter.inspector_digest() == before
