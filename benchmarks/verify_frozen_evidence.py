"""Check the paper's artifact manifest against its source commit and the current tree."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_COMMIT = "6cbfee39af93909a0d3aba77622ea67af63f84c4"


def verify() -> list[str]:
    manifest = json.loads((ROOT / "benchmarks/evidence-round-8.manifest.json").read_text())
    if manifest["source_commit"] != EVIDENCE_COMMIT:
        return ["evidence commit changed"]
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", EVIDENCE_COMMIT, "benchmarks/results", "spec"],
        cwd=ROOT,
    ).decode().splitlines()
    expected = {name for name in names if name.endswith(".json") or name.startswith("spec/")}
    if set(manifest["sha256"]) != expected:
        return ["manifest inventory differs from the frozen commit"]
    failures = []
    for name in sorted(expected):
        original = subprocess.check_output(["git", "show", f"{EVIDENCE_COMMIT}:{name}"], cwd=ROOT)
        digest = hashlib.sha256(original).hexdigest()
        current = ROOT / name
        if manifest["sha256"][name] != digest:
            failures.append(f"manifest hash changed: {name}")
        if not current.is_file() or hashlib.sha256(current.read_bytes().replace(b"\r\n", b"\n")).hexdigest() != digest:
            failures.append(f"frozen artifact changed: {name}")
    return failures


if __name__ == "__main__":
    errors = verify()
    print("\n".join(errors) if errors else "Frozen evidence and manifest match the original source commit.")
    raise SystemExit(bool(errors))
