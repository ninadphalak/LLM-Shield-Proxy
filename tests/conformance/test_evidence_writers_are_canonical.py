"""Every writer that emits published JSON evidence must emit the same bytes anywhere.

`Path.write_text` uses text mode. On Windows that rewrites the newline to CRLF, so an
artifact's SHA-256 records the host that produced it rather than the measurement it
contains. v1 fixed this once in `write_conformance_report`, and then the v2 emitter, the
FIDE emitter and three sweep drivers each reintroduced it. The round-two external review
found the four survivors after the v2-only repair landed.

So this file pins the fix in two directions: the shared writer behaves, AND no evidence
module grows a raw `Path.write_text(json.dumps(...))` again. The second half is the one
that matters, because the first half was already true the last two times this regressed.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pii-leak-benchmark"))
sys.path.insert(0, str(ROOT / "benchmarks"))

from pii_leak_benchmark.artifact import write_json_artifact  # noqa: E402

# The modules that write published JSON evidence. The markdown writers in
# `fide_numeric_audit` and `fide_uncertainty` are deliberately NOT here: they emit
# human-readable text, and normalising them would break the retained
# `UNCERTAINTY.md.json` byte-identity check that the current revision cites. They move
# in the next evidence release, together.
EVIDENCE_WRITERS = (
    "pii-leak-benchmark/pii_leak_benchmark/v2_emitter.py",
    "pii-leak-benchmark/pii_leak_benchmark/fide_emitter.py",
    "pii-leak-benchmark/pii_leak_benchmark/artifact.py",
    "benchmarks/fide_sweep.py",
    "benchmarks/v2_seed_sweep.py",
    "benchmarks/refresh_v2_evidence.py",
)


def test_the_shared_writer_emits_lf_and_one_trailing_newline(tmp_path: Path) -> None:
    path = write_json_artifact(tmp_path / "r.json", {"a": "x", "b": {"c": "y"}})

    payload = path.read_bytes()
    assert b"\r\n" not in payload
    assert payload.endswith(b"}\n")
    assert payload.count(b"\n") == payload.decode().count("\n")


def test_the_shared_writer_preserves_insertion_order_and_indent(tmp_path: Path) -> None:
    """v2 and FIDE publish `indent=1` unsorted. Canonical bytes must not mean resorted."""
    path = write_json_artifact(tmp_path / "r.json", {"zebra": 1, "alpha": 2}, indent=1)

    text = path.read_text(encoding="utf-8")
    assert text.index("zebra") < text.index("alpha")
    assert text.startswith('{\n "zebra"')


def test_the_shared_writer_leaves_no_temporary_behind(tmp_path: Path) -> None:
    """The write is atomic, so an interrupted sweep cannot half-replace a report."""
    write_json_artifact(tmp_path / "r.json", {"a": 1})

    assert [p.name for p in tmp_path.iterdir()] == ["r.json"]


def test_the_shared_writer_creates_missing_parents(tmp_path: Path) -> None:
    path = write_json_artifact(tmp_path / "deep" / "er" / "r.json", {"a": 1})

    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def _raw_json_write_texts(source: str) -> list[int]:
    """Line numbers of every `<expr>.write_text(json.dumps(...))` call in one module."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "write_text"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        # `write_text(json.dumps(x))` and `write_text(json.dumps(x) + "\n")` alike.
        calls = [n for n in ast.walk(first) if isinstance(n, ast.Call)]
        for call in calls:
            if isinstance(call.func, ast.Attribute) and call.func.attr == "dumps":
                found.append(node.lineno)
                break
    return found


@pytest.mark.parametrize("relative", EVIDENCE_WRITERS)
def test_no_evidence_module_writes_json_through_text_mode(relative: str) -> None:
    """The regression that came back twice. Route new writers through `artifact`."""
    path = ROOT / relative
    offenders = _raw_json_write_texts(path.read_text(encoding="utf-8"))

    assert not offenders, (
        f"{relative} writes JSON via Path.write_text at line(s) {offenders}. "
        "Text mode rewrites the newline to CRLF on Windows and makes the artifact's "
        "SHA-256 host-dependent. Use pii_leak_benchmark.artifact.write_json_artifact."
    )


def test_the_detector_would_catch_the_defect_it_is_named_for() -> None:
    """A guard that cannot fail is not a guard. This is the exact pre-fix v2 line."""
    offending = 'path.write_text(json.dumps(report, indent=1), encoding="utf-8")\n'

    assert _raw_json_write_texts(offending) == [1]
    assert _raw_json_write_texts('path.write_text(rendered, encoding="utf-8")\n') == []


def test_fide_run_policy_rejects_unscored_earlier_iterations() -> None:
    """v2 fails loudly on this; FIDE forwarded it to `run_case` and scored the last one."""
    from pii_leak_benchmark import fide_emitter

    with pytest.raises(ValueError, match="exactly one response observation"):
        fide_emitter.run_fide_policy(
            "passthrough", seed="a1b2c3d4e5f60001", iterations=2
        )
