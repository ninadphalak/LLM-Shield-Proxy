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
