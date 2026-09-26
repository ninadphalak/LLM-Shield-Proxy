"""``pii-leak-benchmark submit`` -- post a finished result to the results wall."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # nosec B404 - optional `gh` handoff with a fixed argument list
import sys
import tempfile
import webbrowser
from pathlib import Path
from typing import Any, Optional, Sequence
from urllib.parse import urlencode

from pii_leak_benchmark import report_fields as fields

SUBMISSION_REPO = "ninadphalak/LLM-Shield-Proxy"
SUBMISSION_LABEL = "conformance-result"

# Ordered submission sections.
SUBMISSION_SECTIONS = (
    "Gateway",
    "Version and configuration",
    "Project link",
    "CI run link",
    "How it reads the stream",
    "License",
    "Outcome",
    "Citation block",
    "Notes",
)

# Standard fallback paths for finding a report.
DEFAULT_REPORTS = (
    Path("pii-check") / "current.json",
    Path("current.json"),
    Path("PII_LEAK_BENCHMARK_LATEST.json"),
)


def _verdict(report: dict[str, Any]) -> str:
    """Extract the verdict headline from the report."""
    return str(report.get("verdict") or report.get("outcome") or "")


def _run_url(report: dict[str, Any]) -> str:
    attestation = report.get("attestation") or report.get("provenance") or {}
    return str(attestation.get("run_url") or "") if isinstance(attestation, dict) else ""


def _sections(report: dict[str, Any], *, citation: Optional[str]) -> dict[str, str]:
    name = fields.target_name(report)
    return {
        "Gateway": "" if name == fields.MISSING else name,
        "Version and configuration": (
            "" if fields.target_version(report) == fields.MISSING else fields.target_version(report)
        ),
        "CI run link": _run_url(report),
        "How it reads the stream": "not stated",
        "Outcome": _verdict(report),
        "Citation block": citation or "Paste the output of `pii-leak-benchmark cite` here.",
    }


def build_body(report: dict[str, Any], *, citation: Optional[str] = None) -> str:
    """Build the issue body, embedding the citation if provided."""
    filled = _sections(report, citation=citation)
    return (
        "\n\n".join(
            f"### {heading}\n\n{filled.get(heading, '')}".rstrip()
            for heading in SUBMISSION_SECTIONS
        )
        + "\n"
    )


def build_title(report: dict[str, Any]) -> str:
    name = fields.target_name(report)
    return "Result: " if name == fields.MISSING else f"Result: {name}"


def submission_url(report: dict[str, Any], *, repo: str = SUBMISSION_REPO) -> str:
    """Generate a prefilled issue URL."""
    query = urlencode(
        {
            "title": build_title(report),
            "labels": SUBMISSION_LABEL,
            "body": build_body(report, citation=None),
        }
    )
    return f"https://github.com/{repo}/issues/new?{query}"


def find_report(given: Optional[str]) -> Path:
    if given:
        path = Path(given)
        if not path.is_file():
            raise FileNotFoundError(f"No report at {path}")
        return path
    for candidate in DEFAULT_REPORTS:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "No report found. Pass one, or run this next to "
        + ", ".join(str(path) for path in DEFAULT_REPORTS)
        + "."
    )


def _gh_available() -> bool:
    if not shutil.which("gh"):
        return False
    try:
        return (
            subprocess.run(  # nosec B603 B607 - fixed argument list, resolved via PATH
                ["gh", "auth", "status"],
                capture_output=True,
                timeout=20,
                check=False,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def _create_via_gh(title: str, body: str, repo: str) -> str:
    """Create an issue via `gh`."""
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8", newline="\n"
    )
    try:
        handle.write(body)
        handle.close()
        finished = subprocess.run(  # nosec B603 B607 - fixed argument list, resolved via PATH
            ["gh", "issue", "create", "--repo", repo, "--title", title,
             "--label", SUBMISSION_LABEL, "--body-file", handle.name],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    finally:
        os.unlink(handle.name)
    if finished.returncode != 0:
        raise RuntimeError((finished.stderr or finished.stdout or "").strip())
    return (finished.stdout or "").strip()


EPILOG = """\
Examples:
  pii-leak-benchmark submit                     use the report in the usual place
  pii-leak-benchmark submit pii-check/current.json
  pii-leak-benchmark submit --dry-run           print the submission, send nothing
  pii-leak-benchmark submit --print-url         print the prefilled link, open nothing

With `gh` installed, the issue is created directly. Otherwise, it opens a prefilled browser URL.
The full report is never uploaded to protect sensitive gateway URLs.
"""


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pii-leak-benchmark submit",
        description="Post a finished result to the benchmark results wall.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("report", nargs="?", help="Report to submit; found automatically when omitted")
    parser.add_argument("--repo", default=SUBMISSION_REPO, help="Where to open the issue")
    parser.add_argument("--dry-run", action="store_true", help="Print the submission and exit")
    parser.add_argument("--print-url", action="store_true", help="Print the prefilled link and exit")
    parser.add_argument("--open-browser", action="store_true", help="Skip `gh` and open a browser")
    args = parser.parse_args(argv)

    from pii_leak_benchmark.cite import build_citation

    try:
        path = find_report(args.report)
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("a report must be a JSON object")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Could not read the report: {exc}", file=sys.stderr)
        return 1

    citation = build_citation(report)
    body = build_body(report, citation=citation)
    title = build_title(report)

    if args.dry_run:
        print(f"Report:  {path}")
        print(f"Title:   {title}")
        print(f"Repo:    {args.repo}")
        print()
        print(body)
        return 0

    if args.print_url:
        print(submission_url(report, repo=args.repo))
        return 0

    if not args.open_browser and _gh_available():
        try:
            print(_create_via_gh(title, body, args.repo))
            return 0
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            # Fall back to the browser if `gh` fails.
            print(f"`gh` could not open the issue ({exc}); opening a browser instead.", file=sys.stderr)

    url = submission_url(report, repo=args.repo)
    print("Paste this into the citation section of the issue:\n")
    print(citation)
    if webbrowser.open(url):
        return 0
    print("Could not open a browser. The prefilled issue is here:\n")
    print(url)
    return 2


if __name__ == "__main__":
    sys.exit(main())
