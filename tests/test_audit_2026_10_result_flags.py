"""Audit 2026-10: a truncated dispute list must not erase open-question flags.

Finding scripts/count-result-flags:dispute-list-limit-truncation-erases-flags. ``gh issue list``
returns newest first and stops at ``--limit``; 500 newer junk disputes hid a genuine one, and
the row's flag was removed. A full page is now a read failure that keeps yesterday's numbers.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("flags_audit", REPO_ROOT / "scripts" / "count_result_flags.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("junk", "expected_rc"), [(0, 0), (498, 0), (499, 1), (500, 1), (900, 1)])
def test_a_full_dispute_page_keeps_existing_flags(tmp_path, monkeypatch, junk, expected_rc):
    flags = _load()
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] != "gh":
            raise AssertionError(f"nothing but gh may run: {cmd}")
        limit = int(cmd[cmd.index("--limit") + 1])
        issues = [{"number": 600, "title": "Question: #101", "body": "#101 measurement looks wrong"}]
        issues += [{"number": 601 + i, "title": "Question: ", "body": "x"} for i in range(junk)]
        issues.sort(key=lambda issue: -issue["number"])
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(issues[:limit]), stderr="")

    monkeypatch.setattr(flags.subprocess, "run", fake_run)
    rows = tmp_path / "rows.json"
    original = {"entries": [{"status": "published", "_submission": {"issue": 101}, "flags": {"count": 1, "issue": 101}}]}
    rows.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(flags, "ROWS_FILE", rows)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    assert flags.main(["--repo", "dummy/repo"]) == expected_rc
    assert json.loads(rows.read_text(encoding="utf-8")) == original
    assert len(calls) == 1, "no git commit or push"
