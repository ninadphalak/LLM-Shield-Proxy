"""`pii-leak-benchmark-v2`: the v2 response-split profile as a console command.

`python -m pii_leak_benchmark.v2_emitter` only worked from the repository root, because
`--validate` opened `spec/v2.0.0/http-profile.schema.json` relative to the current
directory. A console command has no repository to stand in, so the schema ships inside the
package and a test here pins the bundled copy to the published one byte for byte.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import tomllib
from pii_leak_benchmark import v2_cli, v2_emitter

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "pii-leak-benchmark"
PUBLISHED_SCHEMA = ROOT / "spec" / "v2.0.0" / "http-profile.schema.json"
BUNDLED_SCHEMA = PACKAGE / "pii_leak_benchmark" / "schemas" / "v2.0.0" / "http-profile.schema.json"

SEED = "a1b2c3d4e5f60001"


def test_the_console_script_is_declared() -> None:
    pyproject = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    assert scripts["pii-leak-benchmark-v2"] == "pii_leak_benchmark.v2_cli:main"
    # The schema is data, and setuptools ships no data it is not told about.
    package_data = pyproject["tool"]["setuptools"]["package-data"]["pii_leak_benchmark"]
    assert any("schemas" in pattern for pattern in package_data)


def test_the_bundled_schema_is_the_published_schema() -> None:
    """Two copies are only acceptable while they are the same bytes."""
    assert BUNDLED_SCHEMA.read_bytes() == PUBLISHED_SCHEMA.read_bytes()


def test_the_schema_loads_from_package_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # nowhere near the repository's spec/ directory
    schema = v2_emitter.load_schema()
    assert schema["$id"] == json.loads(PUBLISHED_SCHEMA.read_text(encoding="utf-8"))["$id"]


def test_an_explicit_schema_path_wins(tmp_path: Path) -> None:
    other = tmp_path / "schema.json"
    other.write_text('{"type": "object", "marker": "explicit"}', encoding="utf-8")
    assert v2_emitter.load_schema(other)["marker"] == "explicit"


def test_the_parser_has_the_v1_ergonomics() -> None:
    parser = v2_cli.build_parser()
    assert parser.prog == "pii-leak-benchmark-v2"
    flags = {action.option_strings[0] for action in parser._actions if action.option_strings}
    assert {"--seed", "--only", "--json-out", "--validate", "--out"} <= flags


def test_the_two_entry_points_share_one_parser() -> None:
    """`python -m pii_leak_benchmark.v2_emitter` and the console command must not drift."""
    module_flags = {a.option_strings[0] for a in v2_emitter.build_parser()._actions if a.option_strings}
    console_flags = {a.option_strings[0] for a in v2_cli.build_parser()._actions if a.option_strings}
    assert module_flags == console_flags


def _one_case_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the covering array to its first case so a CLI test runs in seconds.

    A one-case run cannot satisfy the schema (`cases_scored` is pinned at 32), which the
    fast tests rely on: `--validate` must then report INVALID, not VALID.
    """
    full = v2_emitter.covering_array()
    monkeypatch.setattr(v2_emitter, "covering_array", lambda *a, **k: full[:1])


def test_json_out_rows_mirror_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run from a directory with no spec/ in it: the schema still loads, from the package."""
    _one_case_only(monkeypatch)
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "reports"
    summary_path = tmp_path / "summary.json"
    code = v2_cli.main(
        ["--validate", "--only", "passthrough", "--seed", SEED, "--out", str(out), "--json-out", str(summary_path)]
    )
    assert code == 1, "a one-case run is schema-invalid, and the exit code says so"
    report = json.loads((out / "passthrough.json").read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["schema"] == v2_emitter.SUMMARY_SCHEMA_ID
    assert summary["instrument"] == v2_emitter.instrument_block()
    assert summary["oracle"] == "midpoint"
    (row,) = summary["rows"]
    assert row["policy"] == "passthrough"
    assert row["seed"] == SEED
    assert row["schema_valid"] is False
    assert row["schema_errors"], "the reasons travel with the verdict"
    assert row["outcome"] == report["outcome"]
    # The same keys the published seed sweeps record, so a sweep can be rebuilt from rows.
    assert row["fidelity_rate"] == report["metrics"]["fidelity_rate"]
    assert row["leak_single_chunk"] == report["metrics"]["leak_rate"]["single_chunk"]
    assert row["leak_adversarial"] == report["metrics"]["leak_rate"]["adversarial"]
    assert row["delta_frag"] == report["metrics"]["delta_frag"]
    assert row["cases_applicable"] == report["metrics"]["cases_applicable"]
    assert row["cases_attempted"] == report["metrics"]["cases_scored"]
    assert row["inconclusive"] == report["metrics"]["cases_inconclusive"]
    assert row["echo_observable"] == report["metrics"]["cases_echo_observable"]
    assert row["request_path_leak"] == report["checks"]["configured_upstream_boundary"]["leaked_entity_types"]
    assert row["report"] == "passthrough.json", "relative to --out, never an absolute host path"
    # The summary carries nothing `submit` refuses to print.
    text = summary_path.read_text(encoding="utf-8")
    assert "base_url" not in text and "advertised_url" not in text and "preconfigured" not in text


def test_json_out_without_validate_records_no_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _one_case_only(monkeypatch)
    monkeypatch.chdir(tmp_path)
    summary_path = tmp_path / "summary.json"
    code = v2_cli.main(
        ["--only", "passthrough", "--seed", SEED, "--out", str(tmp_path / "r"), "--json-out", str(summary_path)]
    )
    assert code == 0
    (row,) = json.loads(summary_path.read_text(encoding="utf-8"))["rows"]
    assert row["schema_valid"] is None, "not validated is not the same as valid"
    assert row["schema_errors"] == []


@pytest.mark.slow
def test_a_full_run_validates_from_any_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """All 32 cases of one in-process policy: exit 0 and `schema_valid: true`."""
    monkeypatch.chdir(tmp_path)
    summary_path = tmp_path / "summary.json"
    code = v2_cli.main(
        ["--validate", "--only", "passthrough", "--seed", SEED, "--out", str(tmp_path / "r"), "--json-out", str(summary_path)]
    )
    assert code == 0
    (row,) = json.loads(summary_path.read_text(encoding="utf-8"))["rows"]
    assert row["schema_valid"] is True
    assert row["cases_attempted"] == 32


def test_the_console_script_does_not_import_the_proxy() -> None:
    script = textwrap.dedent(
        """
        import sys
        from pii_leak_benchmark.v2_cli import main
        try:
            main(["--help"])
        except SystemExit:
            pass
        print("PROXY:" + str(any(n == "llm_shield_proxy" or n.startswith("llm_shield_proxy.")
                                 for n in sys.modules)))
        """
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PACKAGE) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True, env=env)
    assert "PROXY:False" in result.stdout
