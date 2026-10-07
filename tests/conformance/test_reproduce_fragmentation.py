"""The reproduction checker must fail on drift, and its ignore list must stay narrow.

`benchmarks/reproduce_fragmentation.py` is what an independent reproducer runs and what
the `fragmentation-reproduction` CI job runs. Its whole value is the exit status, so two
ways of losing that value are pinned here.

The first is an ignore list that grows until it covers a real field. `NONDETERMINISTIC`
suppresses eleven paths recording when, where and how fast the run happened; an entry
covering a rate, a digest or a case count would make the job green on evidence that no
longer reproduces. Every entry must name a leaf that exists in the published report, so a
path that is renamed away cannot linger as a silent blanket.

The list started at eight and was wrong. The three `environment` fields were missing
because it had been measured on the workstation that produced the published reports, where
they matched by coincidence of host; the cross-platform CI matrix found them immediately.
That is the reason for `test_the_ignore_list_is_pinned_by_exact_set_equality`: the set is
not a thing to adjust until the job goes green.

The second is a diff that does not actually see a moved number. These tests do not run
the experiment -- that takes about a minute per policy and belongs in the CI job -- they
run the comparison against mutated copies of the published reports.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
PUBLISHED = ROOT / "benchmarks" / "results" / "v2-response-split"
POLICIES = ("chunk-local", "bounded-retention")


def _load_checker() -> Any:
    """Import the driver by path; `benchmarks/` is a directory, not a package."""
    path = ROOT / "benchmarks" / "reproduce_fragmentation.py"
    spec = importlib.util.spec_from_file_location("reproduce_fragmentation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _published(policy: str) -> dict[str, Any]:
    return json.loads((PUBLISHED / f"{policy}.json").read_text(encoding="utf-8"))


def _leaf_exists(report: Any, dotted: str) -> bool:
    node = report
    for key in dotted.strip(".").split("."):
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return True


@pytest.mark.parametrize("policy", POLICIES)
def test_a_report_compared_with_itself_reports_no_drift(policy: str) -> None:
    report = _published(policy)
    assert list(checker._diff(report, json.loads(json.dumps(report)))) == []


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize(
    "path",
    [
        ("metrics", "leak_rate", "adversarial"),
        ("metrics", "leak_rate", "single_chunk"),
        ("metrics", "fidelity_rate"),
        ("metrics", "cases_applicable"),
        ("corpus", "sha256"),
        ("instrument", "inspector_sha256"),
        ("outcome",),
    ],
)
def test_a_moved_headline_is_reported_as_drift(policy: str, path: tuple[str, ...]) -> None:
    mutated = _published(policy)
    node = mutated
    for key in path[:-1]:
        node = node[key]
    original = node[path[-1]]
    node[path[-1]] = "drifted" if isinstance(original, str) else 0.5
    differences = list(checker._diff(_published(policy), mutated))
    assert [entry[0] for entry in differences] == ["." + ".".join(path)]


@pytest.mark.parametrize("policy", POLICIES)
def test_a_dropped_field_is_reported_as_drift(policy: str) -> None:
    mutated = _published(policy)
    del mutated["metrics"]["delta_frag"]
    assert [entry[0] for entry in checker._diff(_published(policy), mutated)] == [
        ".metrics.delta_frag"
    ]


@pytest.mark.parametrize("policy", POLICIES)
def test_every_ignored_path_names_a_field_the_published_report_has(policy: str) -> None:
    report = _published(policy)
    missing = [path for path in checker.NONDETERMINISTIC if not _leaf_exists(report, path)]
    assert missing == [], f"ignore list names fields absent from {policy}.json: {missing}"


def test_the_ignore_list_is_pinned_by_exact_set_equality() -> None:
    # Named explicitly rather than pattern-matched: a future entry must be added here on
    # purpose, with the reviewer looking at what it hides. Every entry below records when,
    # where or how fast the run happened. None of them records a measurement.
    assert set(checker.NONDETERMINISTIC) == {
        ".generated_at",
        ".environment.python",
        ".environment.implementation",
        ".environment.platform",
        ".capture.self_probe.url",
        ".capture.self_probe.round_trip_ms",
        ".checks.client_observed_latency.mean",
        ".checks.client_observed_latency.p50",
        ".checks.client_observed_latency.p95",
        ".checks.client_observed_latency.p99",
        ".metrics.partition_oracle.partition_seconds_total",
    }


def test_no_metric_digest_or_outcome_path_is_suppressed() -> None:
    """The failure this whole file exists to prevent, stated directly."""
    protected = (".metrics.leak_rate", ".metrics.fidelity_rate", ".metrics.delta_frag",
                 ".metrics.cases_", ".corpus.sha256", ".instrument.", ".outcome", ".passed",
                 ".cases_digest")
    for ignored in checker.NONDETERMINISTIC:
        # `.metrics.partition_oracle.partition_seconds_total` is the one metrics-subtree
        # entry and it is a duration, so match on prefixes that name a measurement.
        assert not any(ignored.startswith(prefix) for prefix in protected), ignored


def test_the_documented_seed_and_policies_match_the_published_reports() -> None:
    assert checker.POLICIES == POLICIES
    for policy in POLICIES:
        assert _published(policy)["corpus"]["seed"] == checker.PUBLISHED_SEED


@pytest.mark.parametrize("policies", [[], ["chunk-local", "chunk-local"], ["../../escape"]])
def test_invalid_selection_is_rejected_before_running(policies, tmp_path):
    with pytest.raises(ValueError):
        checker.load_baselines(policies, tmp_path)


@pytest.mark.parametrize("destination", [PUBLISHED, PUBLISHED.parent, ROOT])
def test_published_outputs_and_ancestors_are_refused(destination):
    with pytest.raises(ValueError, match="published evidence"):
        checker.load_baselines(list(POLICIES), destination)


def test_existing_output_including_hard_links_is_refused(tmp_path):
    import os

    target = tmp_path / "chunk-local.json"
    os.link(PUBLISHED / "chunk-local.json", target)
    with pytest.raises(ValueError, match="already exists"):
        checker.load_baselines(list(POLICIES), tmp_path)


def test_baselines_are_read_before_the_experiment(tmp_path, monkeypatch):
    captured = checker.load_baselines(list(POLICIES), tmp_path)
    assert captured["chunk-local"] == _published("chunk-local")
    # A mutable working-tree baseline cannot change the already loaded comparison.
    captured["chunk-local"]["metrics"]["delta_frag"] = 123
    assert _published("chunk-local")["metrics"]["delta_frag"] == 0.875


def test_changed_baseline_cannot_be_blessed_by_reproduction(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    changed = _published("chunk-local")
    changed["metrics"]["delta_frag"] = 123
    (baseline / "chunk-local.json").write_text(json.dumps(changed))
    monkeypatch.setattr(checker, "PUBLISHED", baseline)
    # Use a scratch manifest with the original digest and a root containing both paths.
    import hashlib

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"source_commit": checker.EVIDENCE_COMMIT,
                                   "sha256": {"baseline/chunk-local.json": hashlib.sha256(
                                       (PUBLISHED / "chunk-local.json").read_bytes().replace(b"\r\n", b"\n")).hexdigest()}}))
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setattr(checker, "MANIFEST", manifest)
    with pytest.raises(ValueError, match="baseline changed"):
        checker.load_baselines(["chunk-local"], tmp_path / "output")



def test_an_output_directory_symlinked_into_published_evidence_is_refused(tmp_path):
    """The alias check resolves the destination, so a link is not a way around it.

    Skipped where the platform will not create one: on Windows an unprivileged process
    cannot, and the guard being untestable there is not the guard being absent.
    """
    import pytest as _pytest

    link = tmp_path / "out"
    try:
        link.symlink_to(PUBLISHED, target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError) as exc:
        _pytest.skip(f"symlinks are not available here: {type(exc).__name__}")
    with pytest.raises(ValueError, match="published evidence"):
        checker.load_baselines(list(POLICIES), link)


def test_a_baseline_rewritten_mid_experiment_cannot_move_the_comparison(tmp_path, monkeypatch):
    """The snapshot is the comparison. A reproduction run takes about a minute per
    policy, and a baseline file rewritten in that window -- by a concurrent checkout, by
    a second run, by an editor -- must not become what the result is judged against.
    The published tree is never touched here: the whole check runs against a copy.
    """
    import hashlib

    baseline = tmp_path / "baseline"
    baseline.mkdir()
    original = (PUBLISHED / "chunk-local.json").read_bytes()
    (baseline / "chunk-local.json").write_bytes(original)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "source_commit": checker.EVIDENCE_COMMIT,
        "sha256": {"baseline/chunk-local.json": hashlib.sha256(
            original.replace(b"\r\n", b"\n")).hexdigest()},
    }))
    monkeypatch.setattr(checker, "PUBLISHED", baseline)
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setattr(checker, "MANIFEST", manifest)

    loaded = checker.load_baselines(["chunk-local"], tmp_path / "output")
    # The experiment is running. The file underneath it changes.
    tampered = json.loads(original)
    tampered["metrics"]["delta_frag"] = 123
    (baseline / "chunk-local.json").write_text(json.dumps(tampered), encoding="utf-8")

    assert loaded["chunk-local"]["metrics"]["delta_frag"] == 0.875
    # A regenerated report matching the REWRITTEN file is drift against the snapshot,
    # which is the only outcome that keeps the exit status meaningful.
    drift = list(checker._diff(loaded["chunk-local"], tampered))
    assert any("delta_frag" in str(entry) for entry in drift), drift
