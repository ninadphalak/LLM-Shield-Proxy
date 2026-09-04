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
