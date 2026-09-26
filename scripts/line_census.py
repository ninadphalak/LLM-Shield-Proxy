#!/usr/bin/env python3
"""Count lines by kind (code, comment, blank) to evaluate change size and comment density.

Usage:
  python scripts/line_census.py                     new files against house style
  python scripts/line_census.py <rev>..<rev>        a range, broken down by kind
"""

from __future__ import annotations

import pathlib
import subprocess  # nosec B404 - git with a fixed argument list
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

HOUSE_STYLE = (
    "pii-leak-benchmark/pii_leak_benchmark/cite.py",
    "pii-leak-benchmark/pii_leak_benchmark/provenance.py",
    "pii-leak-benchmark/pii_leak_benchmark/report_fields.py",
    "pii-leak-benchmark/pii_leak_benchmark/badge.py",
)


def classify(path: pathlib.Path) -> tuple[int, int, int]:
    """Return code, comment, and blank line counts for one Python file. Docstrings count as comments."""
    code = comment = blank = 0
    in_docstring = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        if not stripped:
            blank += 1
            continue
        fences = stripped.count('"""') + stripped.count("'''")
        if in_docstring:
            comment += 1
            if fences % 2:
                in_docstring = False
        elif stripped.startswith(('"""', "'''")):
            comment += 1
            if fences % 2:
                in_docstring = True
        elif stripped.startswith("#"):
            comment += 1
        else:
            code += 1
    return code, comment, blank


def report(label: str, paths: list[pathlib.Path]) -> None:
    total_code = total_comment = 0
    print(f"\n{label}")
    for path in paths:
        if not path.is_file():
            continue
        code, comment, _ = classify(path)
        total_code += code
        total_comment += comment
        share = comment / (code + comment) * 100 if code + comment else 0
        print(f"  {path.name:42} code {code:5}  comment {comment:5}  ({share:.0f}% comment)")
    share = total_comment / (total_code + total_comment) * 100 if total_code + total_comment else 0
    print(f"  {'TOTAL':42} code {total_code:5}  comment {total_comment:5}  ({share:.0f}% comment)")


def bucket(path: str) -> str:
    """Categorize file path by kind (e.g. tests, scripts)."""
    if path.startswith("tests/"):
        return "tests"
    if path.endswith((".md", ".mdx")):
        return "docs and prose"
    if path.startswith(".github/"):
        return "workflows and issue forms"
    if path.startswith("scripts/"):
        return "scripts"
    if path.startswith("website/"):
        return "website code"
    return "packages"


def by_kind(rev_range: str) -> None:
    lines = subprocess.run(  # nosec B603 B607 - fixed argument list, resolved via PATH
        ["git", "diff", "--numstat", rev_range],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    buckets: dict[str, int] = {}
    for line in lines:
        added, _, path = line.split("\t")
        if added == "-":
            continue  # binary
        key = bucket(path.strip())
        buckets[key] = buckets.get(key, 0) + int(added)
    print(f"\nLINES ADDED IN {rev_range}, BY KIND")
    for key, count in sorted(buckets.items(), key=lambda item: -item[1]):
        print(f"  {key:28} {count:6}")
    print(f"  {'TOTAL':28} {sum(buckets.values()):6}")


def main(argv: list[str]) -> int:
    if argv:
        by_kind(argv[0])
        return 0
    changed = subprocess.run(  # nosec B603 B607 - fixed argument list, resolved via PATH
        ["git", "status", "--short"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    new = [REPO_ROOT / p for p in changed if p.endswith(".py")]
    if new:
        report("CHANGED OR UNTRACKED PYTHON", new)
    report("HOUSE STYLE, FOR COMPARISON", [REPO_ROOT / p for p in HOUSE_STYLE])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
