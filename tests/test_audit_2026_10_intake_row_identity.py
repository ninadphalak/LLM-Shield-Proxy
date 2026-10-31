"""Audit 2026-10: a run already on the wall cannot be taken over by another account.

Finding conformance-intake:row-identity-run-url-not-bound-to-submitter. Row identity was the raw
run URL, so a second account linking a victim's public run replaced the victim's row and skipped
the per-account cap. Drives the intake's own main() with GitHub, git, npm and gh stubbed.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN = "https://github.com/victim-org/victim-gw/actions/runs/123456"


@pytest.fixture()
def intake(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "intake_audit", REPO_ROOT / "scripts" / "process_conformance_submission.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def fake_classify(run_url, sink=None):
        if sink is not None:
            sink.update({"runId": 1, "headSha": "0" * 40, "workflowPath": "w.yml",
                         "runCreatedAt": "2026-10-01T00:00:00Z", "ownerType": "Organization"})
        owner, repo, run_id = module.RUN_URL.match(run_url).groups()
        return "submitted-main", "dummy", (owner, repo, run_id)

    def fake_collect(owner, repo, run_id, sink=None):
        if sink is not None:
            sink.update({"name": "pii-leak-benchmark", "id": 1, "bytes": 1, "sha256": "0" * 64})
        return ({"current.json": {"schema": "pii-leak-benchmark/operator-run/v1", "verdict": "CLEAN",
                                  "entities": {"EMAIL": "contained"},
                                  "required_checks": {"response_fidelity": True}}},
                "Read from the artifact.")

    real_process = module.process
    monkeypatch.setattr(module, "process", lambda issue, material=None: real_process(
        issue, classify=fake_classify, collect=fake_collect, material=material))
    module.published = []
    module.comments = []
    monkeypatch.setattr(module, "wait_for_earlier_rows", lambda n: None)
    monkeypatch.setattr(module, "_run", lambda *a, **k: None)
    monkeypatch.setattr(module, "check_row_content", lambda: None)
    monkeypatch.setattr(module, "build_site", lambda: None)
    monkeypatch.setattr(module, "publish", lambda r, n: module.published.append((n, r["project"])) or True)
    monkeypatch.setattr(module, "comment", lambda n, t: module.comments.append((n, t)) or True)
    monkeypatch.setattr(module, "label", lambda n, name: True)
    monkeypatch.setattr(module, "close_issue", lambda n: True)
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "ROWS_FILE", tmp_path / "submitted-rows.json")
    monkeypatch.setattr(module, "ARCHIVE_ROOT", tmp_path / "benchmarks" / "results" / "submitted")
    return module


def _row(issue, submitter, project, run):
    return {"status": "published", "date": "2026-10-01", "project": project, "version": "1",
            "sentN": 0, "sent": "none", "restored": "all", "restoredN": 1.0, "note": "n",
            "provenance": "submitted-main", "architecture": "held-tail", "license": "MIT",
            "runUrl": run, "_submission": {"issue": issue, "submitter": submitter}}


def _main(intake, tmp_path, number, login, run, name="MALLORY-NAME"):
    body = "\n".join(["### Gateway", name, "### Version and configuration", "x",
                      "### How it reads the stream", "buffered", "### License", "Proprietary",
                      "### CI run link", run])
    event = tmp_path / f"ev-{number}.json"
    event.write_text(json.dumps({"issue": {"number": number, "user": {"login": login},
                                           "created_at": "2026-10-31T00:00:00Z", "body": body}}))
    return intake.main(["--event-path", str(event)])


def _seed(intake, entries):
    intake.ROWS_FILE.write_text(json.dumps({"entries": entries}), encoding="utf-8")


def _entries(intake):
    return json.loads(intake.ROWS_FILE.read_text(encoding="utf-8"))["entries"]


@pytest.mark.parametrize(
    "run",
    [
        RUN,
        # Owner and repository are case-insensitive, and a suffix after the run id is the same run.
        "https://github.com/Victim-Org/VICTIM-GW/actions/runs/123456",
        "https://github.com/victim-org/victim-gw/actions/runs/123456/attempts/2",
    ],
)
def test_another_accounts_run_is_refused_and_nothing_is_deleted(intake, tmp_path, run):
    entries = [_row(101, "victim", "Victim Gateway", RUN)]
    entries += [_row(1000 + i, "mallory", f"m{i}", f"https://github.com/mallory/m/actions/runs/{i}")
                for i in range(intake.MAX_ROWS_PER_SUBMITTER)]
    _seed(intake, entries)

    assert _main(intake, tmp_path, 202, "mallory", run) == 1
    assert intake.published == []
    assert _entries(intake) == entries, "the victim's row and every other row are untouched"
    assert intake.comments and "another account" in intake.comments[-1][1]


def test_a_run_claimed_by_another_account_is_refused_below_the_cap_too(intake, tmp_path):
    entries = [_row(101, "victim", "Victim Gateway", RUN)]
    _seed(intake, entries)
    assert _main(intake, tmp_path, 202, "Mallory", RUN) == 1
    assert _entries(intake) == entries


def test_the_same_submitter_can_still_replace_their_own_run_from_a_new_issue(intake, tmp_path):
    _seed(intake, [_row(101, "Victim", "Victim Gateway", RUN)])
    # Login compared case-insensitively.
    assert _main(intake, tmp_path, 303, "victim", RUN.replace("victim-org", "VICTIM-org"), name="Renamed") == 0
    rows = _entries(intake)
    assert [(r["_submission"]["issue"], r["project"]) for r in rows] == [(303, "Renamed")]


def test_the_same_issue_still_replaces_its_row_with_a_rerun(intake, tmp_path):
    _seed(intake, [_row(101, "victim", "Victim Gateway", RUN)])
    rerun = "https://github.com/victim-org/victim-gw/actions/runs/123457"
    assert _main(intake, tmp_path, 101, "victim", rerun, name="Victim Gateway") == 0
    assert [r["runUrl"] for r in _entries(intake)] == [rerun]


def test_append_row_never_drops_another_accounts_row_on_the_same_run(intake, tmp_path):
    path = tmp_path / "rows.json"
    victim = _row(101, "victim", "Victim Gateway", RUN)
    path.write_text(json.dumps({"entries": [victim]}), encoding="utf-8")
    intake.append_row(_row(202, "mallory", "MALLORY-NAME", RUN), path=path)
    assert victim in json.loads(path.read_text(encoding="utf-8"))["entries"]


def test_the_canonical_run_key():
    assert intake_key("https://github.com/A/B/actions/runs/0012") == "a/b/12"
    assert intake_key("https://github.com/A/B/actions/runs/12?pr=1#x") == "a/b/12"
    assert intake_key("https://example.com/a/b/actions/runs/1") == ""


def intake_key(url):
    spec = importlib.util.spec_from_file_location(
        "intake_key", REPO_ROOT / "scripts" / "process_conformance_submission.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.canonical_run_key(url)
