"""Replication packs: `benchmarks/replication/<gateway>/` reproduces a published row in one command.

A pack is a compose file, the exact configuration the published row used, and a runner
that replays the row's seeds and compares every number to the committed sweep. These
tests pin the parts a reader cannot see at a glance: that every image is a digest, that
the configuration is the published file and not a rewrite, that the runner mounts the
published numbers it compares against, and that the comparison itself is honest.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
REPLICATION = ROOT / "benchmarks" / "replication"
RESULTS = ROOT / "benchmarks" / "results" / "v2-response-split"
PACKS = sorted(p for p in REPLICATION.iterdir() if p.is_dir() and (p / "docker-compose.yml").exists())

# pack -> (policy, published sweep, published single-seed report, {pack file: source file})
PUBLISHED = {
    "litellm-presidio": (
        "litellm-presidio",
        "seed-sweep-litellm.json",
        "litellm-presidio.json",
        {"config.docker.yaml": ROOT / "benchmarks" / "litellm-v2-profile" / "config.docker.yaml"},
    ),
    "nemo-guardrails": (
        "nemo-guardrails-0.24.0",
        "seed-sweep-nemo.json",
        "nemo-guardrails-0.24.0.json",
        {"config/config.yml": ROOT / "benchmarks" / "nemo-v2-profile" / "config" / "config.yml"},
    ),
}


def _load_replicate():
    spec = importlib.util.spec_from_file_location("replicate", REPLICATION / "runner" / "replicate.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _compose(pack: Path) -> dict:
    return yaml.safe_load((pack / "docker-compose.yml").read_text(encoding="utf-8"))


def test_every_pack_is_documented_here() -> None:
    assert {p.name for p in PACKS} == set(PUBLISHED), "add the new pack to PUBLISHED so its files are pinned"


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.name)
def test_pack_has_the_three_parts(pack: Path) -> None:
    assert (pack / "README.md").exists()
    assert (pack / ".gitignore").read_text(encoding="utf-8").splitlines() == ["out/"], "runs never get committed"
    services = _compose(pack)["services"]
    assert "runner" in services


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.name)
def test_every_pulled_image_is_pinned_by_digest(pack: Path) -> None:
    for name, service in _compose(pack)["services"].items():
        if "image" in service:
            assert "@sha256:" in service["image"], f"{name} is pulled by tag; a tag moves"
    # A gateway built here rather than pulled pins its base image the same way.
    dockerfile = pack / "Dockerfile"
    if dockerfile.exists():
        froms = [line for line in dockerfile.read_text(encoding="utf-8").splitlines() if line.startswith("FROM ")]
        assert froms and all("@sha256:" in line for line in froms), "the base image is pulled by tag"


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.name)
def test_configuration_files_are_the_published_ones(pack: Path) -> None:
    _, _, _, copies = PUBLISHED[pack.name]
    for pack_file, source in copies.items():
        assert (pack / pack_file).read_bytes() == source.read_bytes(), f"{pack_file} was rewritten, not copied"


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.name)
def test_runner_compares_against_the_committed_row(pack: Path) -> None:
    policy, sweep, report, _ = PUBLISHED[pack.name]
    runner = _compose(pack)["services"]["runner"]
    mounts = {v.split(":")[0]: v.split(":")[1] for v in runner["volumes"]}
    assert mounts["../runner/replicate.py"] == "/replicate.py"
    assert mounts[f"../../results/v2-response-split/{sweep}"] == "/expected/seed-sweep.json"
    assert mounts[f"../../results/v2-response-split/{report}"] == "/expected/report.json"
    assert mounts["./out"] == "/out", "the report lands in ./out on the host"
    command = runner["command"]
    assert command[command.index("--policy") + 1] == policy
    assert command[command.index("--expected") + 1] == "/expected/seed-sweep.json"
    assert command[command.index("--expected-report") + 1] == "/expected/report.json"
    # The runner is built from this checkout's harness, so the instrument is the checkout's.
    assert runner["build"]["context"] == "../../../pii-leak-benchmark"


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.name)
def test_runner_carries_the_measured_environment(pack: Path) -> None:
    """`V2_REQUEST_PATH_REDACTION` is part of the configuration the row was measured under."""
    _, _, report, _ = PUBLISHED[pack.name]
    published = json.loads((RESULTS / report).read_text(encoding="utf-8"))
    env = _compose(pack)["services"]["runner"]["environment"]
    assert env["V2_REQUEST_PATH_REDACTION"] == published["redaction_claim"]["request_path_redaction_configured"]


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.name)
def test_readme_states_the_command_and_the_published_numbers(pack: Path) -> None:
    policy, sweep, _, _ = PUBLISHED[pack.name]
    text = (pack / "README.md").read_text(encoding="utf-8")
    assert "docker compose up --build --exit-code-from runner" in text
    block = json.loads((RESULTS / sweep).read_text(encoding="utf-8"))[policy]
    for metric in ("leak_single_chunk", "leak_adversarial", "delta_frag", "fidelity_rate"):
        assert str(block["summary"][metric]["mean"]) in text, f"README does not state the published {metric}"
    assert block["instrument"]["inspector_sha256"] in text


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.name)
def test_aggregate_rebuilds_the_published_sweep_from_its_own_runs(pack: Path) -> None:
    """The runner's statistics are `v2_seed_sweep.py`'s, rounding included."""
    replicate = _load_replicate()
    policy, sweep, _, _ = PUBLISHED[pack.name]
    block = json.loads((RESULTS / sweep).read_text(encoding="utf-8"))[policy]
    rebuilt = replicate.aggregate(policy, block["runs"], block["seeds"], block["instrument"])
    assert rebuilt == block


def test_compare_sweep_is_silent_on_identical_blocks_and_loud_on_one_changed_rate() -> None:
    replicate = _load_replicate()
    block = json.loads((RESULTS / "seed-sweep-litellm.json").read_text(encoding="utf-8"))["litellm-presidio"]
    assert replicate.compare_sweep(block, copy.deepcopy(block)) == []

    changed = copy.deepcopy(block)
    changed["runs"][0]["leak_adversarial"] = 0.0
    changed = replicate.aggregate("litellm-presidio", changed["runs"], changed["seeds"], changed["instrument"])
    differences = replicate.compare_sweep(block, changed)
    assert any(d.startswith(f"seed {block['runs'][0]['seed']} leak_adversarial") for d in differences)
    assert any(d.startswith("summary leak_adversarial.mean") for d in differences)

    other_instrument = copy.deepcopy(block)
    other_instrument["instrument"]["inspector_sha256"] = "0000000000000000"
    assert any("inspector_sha256" in d for d in replicate.compare_sweep(block, other_instrument))

    missing = copy.deepcopy(block)
    missing["runs"] = missing["runs"][1:]
    assert any(d.endswith("not replayed") for d in replicate.compare_sweep(block, missing))


def test_compare_report_reads_the_wall_fields() -> None:
    replicate = _load_replicate()
    report = json.loads((RESULTS / "litellm-presidio.json").read_text(encoding="utf-8"))
    assert replicate.compare_report(report, copy.deepcopy(report)) == []

    changed = copy.deepcopy(report)
    changed["checks"]["configured_upstream_boundary"]["leaked_entity_types"] = []
    changed["metrics"]["by_axis"]["fragmentation"]["adversarial"]["leaked"] = 3
    differences = replicate.compare_report(report, changed)
    assert any(d.startswith("checks.configured_upstream_boundary.leaked_entity_types") for d in differences)
    assert any(d.startswith("metrics.by_axis.fragmentation.adversarial.leaked") for d in differences)


def test_one_line_names_the_verdict_and_the_inspector() -> None:
    replicate = _load_replicate()
    block = json.loads((RESULTS / "seed-sweep-litellm.json").read_text(encoding="utf-8"))["litellm-presidio"]
    line = replicate.one_line("litellm-presidio", block, [])
    assert line.startswith("litellm-presidio: 6 seeds")
    assert "leak(adv) 0.0625 [0-0.1875]" in line
    assert "request-path leak CARDPAN,EMAIL,SSN,USPHONE" in line
    assert "outcome fail" in line
    assert block["instrument"]["inspector_sha256"] in line
    assert line.endswith("MATCHES the published row")
    assert "DIFFERS from the published row (2)" in replicate.one_line("litellm-presidio", block, ["a", "b"])
