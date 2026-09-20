#!/usr/bin/env python3
"""Count open disputes against each published row.

A count, never a verdict: a disputed row is never hidden or downranked, which is the same
rule that keeps two disagreeing runs both on the page. An open issue labelled
`result-dispute` whose body names a row's submission issue counts against it, so the count
comes from the tracker each run and clears itself when the issue closes.

Standard library plus `gh`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404 - gh and git with fixed argument lists
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
ROWS_FILE = REPO_ROOT / "website" / "src" / "data" / "submitted-rows.json"
DISPUTE_LABEL = "result-dispute"


def open_disputes(repo: str) -> list[dict[str, Any]]:
    finished = subprocess.run(  # nosec B603 B607 - fixed argument list, resolved via PATH
        ["gh", "issue", "list", "--repo", repo, "--label", DISPUTE_LABEL,
         "--state", "open", "--limit", "500", "--json", "number,body,title"],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if finished.returncode != 0:
        raise RuntimeError((finished.stderr or "").strip()[:300])
    return json.loads(finished.stdout or "[]")


def count_by_issue(disputes: list[dict[str, Any]]) -> dict[int, int]:
    """How many open disputes name each submission issue.

    A dispute that names several rows counts against each of them, which is right: one
    issue saying "these three rows all used the wrong config" is a question about three
    rows. The reference has to be a `#123` token, so a bare number in prose ("16 of 16")
    cannot be mistaken for one.
    """
    counts: dict[int, int] = {}
    for dispute in disputes:
        text = f"{dispute.get('title', '')}\n{dispute.get('body', '') or ''}"
        for referenced in {int(n) for n in re.findall(r"#(\d{1,7})\b", text)}:
            if referenced == dispute.get("number"):
                continue  # an issue referring to itself is not a dispute of a row
            counts[referenced] = counts.get(referenced, 0) + 1
    return counts


def apply_counts(document: dict[str, Any], counts: dict[int, int]) -> int:
    """Write the counts onto the rows. Returns how many rows changed."""
    changed = 0
    for entry in document.get("entries", []):
        if not isinstance(entry, dict):
            continue
        issue = (entry.get("_submission") or {}).get("issue")
        if not isinstance(issue, int):
            continue
        count = counts.get(issue, 0)
        before = entry.get("flags")
        after = {"count": count, "issue": issue} if count else None
        if before == after:
            continue
        if after:
            entry["flags"] = after
        else:
            entry.pop("flags", None)
        changed += 1
    return changed


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default="ninadphalak/LLM-Shield-Proxy")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        counts = count_by_issue(open_disputes(args.repo))
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        # A failure here must never take the site down or empty the counts. Leaving
        # yesterday's numbers up is strictly better than publishing zeroes we did not
        # measure.
        print(f"Could not read the dispute list: {exc}", file=sys.stderr)
        return 1

    document = json.loads(ROWS_FILE.read_text(encoding="utf-8"))
    changed = apply_counts(document, counts)
    print(f"{len(counts)} referenced issues, {changed} rows changed")
    # The calling workflow rebuilds and redeploys only when something moved, because a
    # push made with GITHUB_TOKEN does not start deploy-docs.yml. Telling it whether to
    # bother is cheaper than deploying an unchanged site every night.
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"changed={'true' if changed and not args.dry_run else 'false'}\n")
    if args.dry_run or not changed:
        if args.dry_run:
            print(json.dumps(document.get("entries", []), indent=2, ensure_ascii=False))
        return 0

    ROWS_FILE.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    for command in (
        ["git", "config", "user.name", "github-actions[bot]"],
        ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
        ["git", "add", str(ROWS_FILE.relative_to(REPO_ROOT))],
        ["git", "commit", "-m", f"chore(results-wall): refresh open question counts ({changed} rows)"],
        ["git", "push", "origin", "HEAD:main"],
    ):
        subprocess.run(command, check=True, cwd=REPO_ROOT)  # nosec B603 - fixed argument lists
    return 0


if __name__ == "__main__":
    sys.exit(main())
