#!/usr/bin/env python3
"""Turn a benchmark result posted as an issue into a published row, without a person.

THE SUBMISSION IS NOT THE EVIDENCE. The issue supplies the three facts no report records:
the gateway's name, its licence, and how it reads a streaming response. Everything
measured is read from the REPORT ARTIFACT of the CI run the submitter linked, downloaded
here from the GitHub API. A pasted citation block is a claim; an artifact attached to a
run in a named repository on a named branch is a thing a reader can go and check, and it
is what every number on the wall comes from.

That is also what makes the verification real rather than a formality. Nobody reads the
numbers off the issue and retypes them, so there is nothing to mistype and nothing to
forge that would not also have to be forged in a public Actions run.

WHAT IS STILL NOT AUTOMATED, AND CANNOT BE. The two response-split columns are produced by
a different profile from the one that runs in a gateway's CI. When the artifact does not
contain them the row leaves them unset and the table prints "not measured". They are never
defaulted to zero: `0 of 16` is the strongest claim the page makes.

WHY IT VALIDATES BY BUILDING. Two GitHub mechanisms mean a pull request here would be
checked by nothing: a PR opened with GITHUB_TOKEN triggers no workflows at all, and the
site build runs on push to main rather than on pull requests. So this script runs the real
site build itself, before it commits. The gate is the same gate, moved to the only place it
actually executes.

Standard library only, plus `gh` for the calls that need it. Nothing here imports either
distribution: it runs on a bare runner.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess  # nosec B404 - git, gh and npm with fixed argument lists
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
ROWS_FILE = REPO_ROOT / "website" / "src" / "data" / "submitted-rows.json"
WEBSITE = REPO_ROOT / "website"

# Heading text to field id. An issue form renders each field as `### <label>` followed by
# the value, so the labels here must stay identical to the ones in
# `.github/ISSUE_TEMPLATE/conformance-result.yml`, and to the ones the prefilled link in
# `submit.py` writes. One parser reads both paths; `tests/test_conformance_intake.py` pins
# them together so a renamed label fails a test rather than silently dropping a field.
FIELD_BY_HEADING = {
    "gateway": "gateway",
    "version and configuration": "version",
    "project link": "project_url",
    "ci run link": "run_url",
    "how it reads the stream": "architecture",
    "license": "license",
    "outcome": "outcome",
    "citation block": "citation",
    "notes": "notes",
}

# What the issue must carry. The measured fields are deliberately absent: they come from
# the artifact, and asking for them would invite a mismatch between the two.
REQUIRED_FIELDS = ("gateway", "version", "architecture", "license", "run_url")

# The `Architecture` union in `website/src/data/results-wall.ts`. A dropdown option carries
# its union value in parentheses so a reader of the issue sees prose and the parser still
# gets an exact token.
ARCHITECTURES = ("per-chunk", "buffered", "held-tail", "none", "not-stated")

# What an issue form writes into an optional field nobody filled in.
EMPTY_MARKERS = ("_no response_", "_none_", "n/a", "none", "")

RUN_URL = re.compile(
    r"^https://github\.com/([A-Za-z0-9._-]{1,100})/([A-Za-z0-9._-]{1,100})/actions/runs/(\d{1,20})"
)

# Entity ids as a sentence says them. An id that is not here is printed as-is with
# underscores opened out, which reads acceptably for anything the corpus adds later.
ENTITY_WORDS = {
    "EMAIL": "email addresses",
    "SSN": "social security numbers",
    "PHONE": "phone numbers",
    "CREDIT_CARD": "card numbers",
    "AWS_ACCESS_KEY_ID": "AWS keys",
    # nosec B105 on both: these are display labels for entity IDs, keyed by the name the
    # corpus uses. There is no credential here, only the English for one.
    "GITHUB_TOKEN": "GitHub tokens",  # nosec B105
    "SLACK_TOKEN": "Slack tokens",  # nosec B105
}

MAX_FIELD_CHARS = 200
API_TIMEOUT_SECONDS = 20
# An operator report is a few hundred kilobytes. Anything far past that is not one, and
# unzipping it on a runner is not something an issue should be able to ask for.
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024
# A benchmark artifact holds four files. These are not tuned limits, they are a refusal to
# iterate over an archive somebody else decided the shape of.
MAX_MEMBERS = 500
MAX_REPORTS = 25

# How many rows one account may hold at once. Not a judgement about anybody: a gateway
# maintainer has one row per version they care about, and a number well past that is the
# signature of a flood rather than of a prolific contributor. Past it, submissions are
# answered rather than published, which costs an honest heavy user a sentence and costs
# somebody opening issues in a loop everything.
MAX_ROWS_PER_SUBMITTER = 12


# --------------------------------------------------------------------------- parsing


def parse_submission(body: str) -> dict[str, str]:
    """Split an issue body into fields, by its `### ` headings.

    Tolerant on purpose: a submitter may have typed the body by hand, reordered the
    sections, or left an optional one out. An unknown heading is ignored rather than
    treated as an error, because refusing a submission over an extra section would cost
    us the result and teach the submitter nothing.
    """
    fields: dict[str, str] = {}
    current: Optional[str] = None
    collected: list[str] = []

    def flush() -> None:
        if current is None:
            return
        value = "\n".join(collected).strip()
        fields[current] = "" if value.strip().casefold() in EMPTY_MARKERS else value

    for raw_line in (body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        heading = re.match(r"^\s{0,3}#{2,4}\s+(.+?)\s*$", raw_line)
        if heading:
            flush()
            current = FIELD_BY_HEADING.get(heading.group(1).strip().casefold())
            collected = []
            continue
        if current is not None:
            collected.append(raw_line)
    flush()
    return fields


def normalize_architecture(value: str) -> Optional[str]:
    """Map a dropdown option to its union value, or None when it is not one of them."""
    text = value.strip().casefold()
    parenthesised = re.search(r"\(([a-z-]+)\)\s*$", text)
    if parenthesised and parenthesised.group(1) in ARCHITECTURES:
        return parenthesised.group(1)
    for option in ARCHITECTURES:
        if text == option or text == option.replace("-", " "):
            return option
    if text in ("not stated", "unknown", ""):
        return "not-stated"
    return None


def clean(value: str, *, limit: int = MAX_FIELD_CHARS) -> str:
    """Make a submitted string safe to publish, without changing what it says.

    Three jobs. Control characters and line breaks come out, because a row renders in one
    table cell and a newline in a JSON string is a field that looks like two. The em dash
    goes to a hyphen, because public docs ban it and `tests/test_public_docs_style.py`
    reads this file's output; substituting is better than rejecting a submission over
    punctuation, and better than finding out at build time. And the value is capped,
    because a cell is not a place to publish an essay.

    What it deliberately does NOT do is escape quotes or angle brackets. The value ends up
    in JSON, where `json.dump` escapes what needs escaping, and then in React, which
    escapes text. Doing it again here would publish `&amp;quot;` on the page.
    """
    text = unicodedata.normalize("NFC", value or "")
    text = "".join(" " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch for ch in text)
    text = text.replace("—", "-")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].strip() if len(text) > limit else text


# ------------------------------------------------------------------- the GitHub API


def _api(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "llm-shield-proxy-conformance-intake",
            **(
                {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"}
                if os.getenv("GITHUB_TOKEN")
                else {}
            ),
        },
    )
    # The URL is always rebuilt from a matched owner/repo/id against api.github.com, so
    # there is no caller-controlled scheme or host here.
    with urllib.request.urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))


def _download_zip(owner: str, repo: str, artifact_id: int) -> bytes:
    """Fetch an artifact through `gh`, which is the part urllib gets wrong.

    The artifact endpoint answers with a redirect to signed blob storage, and that host
    rejects a request that still carries the API's Authorization header. Python's redirect
    handler forwards the header, so the download fails with a 403 that reads like a
    permissions problem and is not one. `gh api` handles the handover correctly.
    """
    # STREAMED TO DISK, NOT INTO MEMORY. This used `capture_output=True` and then checked
    # `len(stdout)` against the cap, which is a guard that runs after the allocation it
    # exists to prevent: the whole artifact was already buffered by the time the cap was
    # consulted. The artifact is named by the submitter and can be any size they like.
    #
    # The size is now refused twice: from the listing before anything is fetched, and from
    # the file on disk after, because a listing is a claim and the bytes are the fact.
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
        destination = Path(handle.name)
    try:
        with destination.open("wb") as sink:
            finished = subprocess.run(  # nosec B603 B607 - fixed argument list, resolved via PATH
                ["gh", "api", f"repos/{owner}/{repo}/actions/artifacts/{artifact_id}/zip"],
                stdout=sink,
                stderr=subprocess.PIPE,
                timeout=180,
                check=False,
            )
        if finished.returncode != 0:
            raise RuntimeError((finished.stderr or b"").decode("utf-8", "replace").strip()[:300])
        if destination.stat().st_size > MAX_ARTIFACT_BYTES:
            raise RuntimeError("artifact is larger than this job will unpack")
        return destination.read_bytes()
    finally:
        destination.unlink(missing_ok=True)


def reports_from_zip(data: bytes) -> dict[str, Any]:
    """Every JSON report in an artifact, by its file name.

    Bounded on the way in. An artifact is a zip an outside repository produced, so its
    member sizes are not this job's to trust, and `zipfile` will happily expand a small
    file into a large one.
    """
    found: dict[str, Any] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        # Member COUNT is capped as well as member size. A benchmark artifact holds four
        # files; an archive of a hundred thousand tiny ones is not one, and without this
        # the loop is as long as a stranger cares to make it.
        for info in archive.infolist()[:MAX_MEMBERS]:
            name = Path(info.filename).name
            if not name.endswith(".json") or info.file_size > MAX_MEMBER_BYTES:
                continue
            try:
                found[name] = json.loads(archive.read(info).decode("utf-8"))
            except (ValueError, UnicodeDecodeError, zipfile.BadZipFile):
                continue
            if len(found) >= MAX_REPORTS:
                break
    return found


def collect_reports(
    owner: str,
    repo: str,
    run_id: str,
    *,
    api: Callable[[str], Any] = _api,
    download: Callable[[str, str, int], bytes] = _download_zip,
) -> tuple[dict[str, Any], str]:
    """The JSON reports attached to a run, and a sentence about how it went.

    Never raises. Every failure here (an expired artifact, a private repository, a token
    without cross-repository read, a run with nothing attached) ends the same way: no
    reports, and a row that claims less. None of them is evidence of bad faith.
    """
    try:
        listing = api(f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {}, f"The run's artifacts could not be listed ({type(exc).__name__})."
    artifacts = listing.get("artifacts") if isinstance(listing, dict) else None
    if not artifacts:
        return {}, "The run has no artifacts attached, so there was no report to read."

    problems = []
    for artifact in artifacts:
        if artifact.get("expired"):
            problems.append(f"`{artifact.get('name')}` has expired")
            continue
        # Refuse on the listed size before fetching anything. The download checks the real
        # bytes afterwards, because this number is the submitter's claim about their own
        # artifact, but declining here means an oversized one is never fetched at all.
        declared = artifact.get("size_in_bytes")
        if isinstance(declared, int) and declared > MAX_ARTIFACT_BYTES:
            problems.append(f"`{artifact.get('name')}` is larger than this job will fetch")
            continue
        try:
            reports = reports_from_zip(download(owner, repo, int(artifact["id"])))
        except (RuntimeError, OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
            problems.append(f"`{artifact.get('name')}` could not be read ({type(exc).__name__})")
            continue
        if any(is_operator_run(report) or is_response_split(report) for report in reports.values()):
            return reports, f"Read from the artifact `{artifact.get('name')}` on that run."
    if problems:
        return {}, "No report could be read: " + "; ".join(problems[:3]) + "."
    return {}, "The run's artifacts contain no benchmark report."


# --------------------------------------------------------------------- provenance


def classify_provenance(
    run_url: str, fetch: Callable[[str], Any] = _api
) -> tuple[str, str, Optional[tuple[str, str, str]]]:
    """Decide the "Who ran it" value from the run the submitter pointed at.

    Returns one of the five `Provenance` values in `results-wall.ts`, a sentence saying
    why, and the run's coordinates when they parsed.

    WHAT THIS PROVES. That a run exists at that URL, on that branch, in that repository.
    Combined with reading the report out of that run's own artifact, it also proves the
    numbers came from there. It does not prove the gateway was the version claimed, which
    nothing outside that repository can.

    `measured-here` is never returned: it means this project ran it, and this code path is
    by definition somebody else.
    """
    if not run_url:
        return "submitted-unverified", "No run was linked, so the result is taken at face value.", None
    match = RUN_URL.match(run_url.strip())
    if not match:
        return (
            "submitted-unverified",
            "The run link is not a github.com Actions run URL, so it could not be checked.",
            None,
        )
    owner, repo, run_id = match.groups()
    try:
        run = fetch(f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return (
            "submitted-unverified",
            f"The run could not be read ({type(exc).__name__}). A private repository or a "
            "deleted run looks the same from here, so nothing is inferred from it.",
            None,
        )
    if not isinstance(run, dict):
        return "submitted-unverified", "The run API returned something unreadable.", None

    head_repo = run.get("head_repository") or {}
    home_repo = run.get("repository") or {}
    head_name = str(head_repo.get("full_name") or "")
    home_name = str(home_repo.get("full_name") or f"{owner}/{repo}")
    branch = str(run.get("head_branch") or "")
    where = (owner, repo, run_id)

    # SECOND CALL, and it is not avoidable. The `repository` object embedded in a workflow
    # run is the minimal form and carries no `default_branch`: measured against a real run,
    # it has 46 keys and that is not one of them. Reading it from there returned empty, so
    # a run on `main` failed the default-branch comparison and was published as
    # `submitted-branch`, quietly understating every submission from a project's own
    # trunk. The repository endpoint does have the field.
    default_branch = str(home_repo.get("default_branch") or "")
    if not default_branch:
        try:
            details = fetch(f"https://api.github.com/repos/{owner}/{repo}")
            default_branch = str((details or {}).get("default_branch") or "")
        except (urllib.error.URLError, OSError, ValueError):
            default_branch = ""

    if head_repo.get("fork") or (head_name and head_name != home_name):
        return (
            "submitted-fork",
            f"The run came from the fork `{head_name or 'unknown'}`, so it says what a "
            "change does rather than what the released code does.",
            where,
        )
    if branch and default_branch and branch == default_branch:
        return "submitted-main", f"The run is on `{branch}`, the default branch of `{home_name}`.", where
    if branch:
        return (
            "submitted-branch",
            f"The run is on `{branch}`, which is not the default branch of `{home_name}`, "
            "so it may not be shipping code.",
            where,
        )
    return "submitted-unverified", "The run exists but records no branch.", where


# --------------------------------------------------------- reading the measurements


def is_operator_run(report: Any) -> bool:
    return isinstance(report, dict) and str(report.get("schema", "")).startswith(
        "pii-leak-benchmark/operator-run"
    )


def is_response_split(report: Any) -> bool:
    """A profile that scored the response, whole and split across two chunks."""
    if not isinstance(report, dict):
        return False
    leak = (report.get("metrics") or {}).get("leak_rate")
    return isinstance(leak, dict) and "single_chunk" in leak and "adversarial" in leak


def _words(entities: list[str]) -> str:
    said = [ENTITY_WORDS.get(name, name.replace("_", " ").lower()) for name in entities]
    if len(said) == 1:
        return said[0]
    return ", ".join(said[:-1]) + " and " + said[-1]


def derive_measurements(reports: dict[str, Any]) -> dict[str, Any]:
    """Every wall column the submitted reports actually establish.

    Reads the operator run for what reached the provider and the raw report for the
    fidelity check, because the operator run drops that check under the anonymize duty
    while the raw report records it either way. Reads a response-split report for the two
    leak columns when one is present, and leaves them out entirely when it is not.
    """
    operator = next((r for r in reports.values() if is_operator_run(r)), None)
    split = next((r for r in reports.values() if is_response_split(r)), None)
    raw = next(
        (
            r
            for name, r in reports.items()
            if isinstance(r, dict) and "checks" in r and not is_operator_run(r) and "raw" in name
        ),
        None,
    )
    derived: dict[str, Any] = {}

    if operator:
        entities = operator.get("entities") or {}
        leaked = sorted(name for name, state in entities.items() if state == "leak")
        measured = sorted(name for name, state in entities.items() if state != "not measured")
        derived["sentN"] = len(leaked)
        if not leaked:
            derived["sent"] = "none"
        elif measured and len(leaked) == len(measured):
            derived["sent"] = f"all {len(measured)} types"
        elif len(leaked) <= 2:
            derived["sent"] = _words(leaked)
        else:
            derived["sent"] = f"{len(leaked)} of {len(measured)} types"
        derived["_leaked"] = leaked
        derived["_measured"] = measured

    fidelity = None
    if raw:
        value = ((raw.get("checks") or {}).get("response_fidelity") or {}).get("passed")
        fidelity = value if isinstance(value, bool) else None
    if fidelity is None and operator:
        value = (operator.get("required_checks") or {}).get("response_fidelity")
        fidelity = value if isinstance(value, bool) else None
    if fidelity is not None:
        derived["restored"] = "all" if fidelity else "none"
        derived["restoredN"] = 1.0 if fidelity else 0.0
    elif split:
        # A RATE IS NOT A BOOLEAN. This read `bool(rate)`, so a fidelity rate of 0.75 was
        # truthy and published as "all" with `restoredN: 1.0` and a note saying every
        # value came back. That is the strongest claim this column can make, asserted from
        # a measurement that says the opposite, and it is reachable from any profile that
        # restores some cases and not others.
        #
        # The two checks above are genuinely boolean: a run either reconstructed the
        # expected value or it did not. Only the response-split profile reports a rate
        # across a case set, and a partial one is reported as partial.
        rate = (split.get("metrics") or {}).get("fidelity_rate")
        if isinstance(rate, (int, float)) and not isinstance(rate, bool) and 0.0 <= rate <= 1.0:
            derived["restored"] = "all" if rate == 1.0 else "none" if rate == 0.0 else "some"
            derived["restoredN"] = float(rate)

    if split:
        metrics = split.get("metrics") or {}
        leak = metrics.get("leak_rate") or {}
        counts = metrics.get("cases_by_condition") or {}
        for key, condition, text, number in (
            ("single_chunk", "single_chunk", "leakWhole", "leakWholeN"),
            ("adversarial", "adversarial", "leakSplit", "leakSplitN"),
        ):
            rate = leak.get(key)
            total = counts.get(condition)
            if isinstance(rate, (int, float)) and isinstance(total, int) and total > 0:
                derived[text] = f"{round(rate * total)} of {total}"
                derived[number] = float(rate)
    return derived


def write_note(derived: dict[str, Any]) -> str:
    """One sentence about the run, assembled from what the run recorded.

    Every clause is a restatement of a measured field. Nothing here characterises the
    product, and nothing is said that the reports did not establish: a run with no
    fidelity check produces a sentence with no fidelity clause.
    """
    parts: list[str] = []
    leaked = derived.get("_leaked")
    if leaked is not None:
        parts.append(
            "Kept everything out of the provider request."
            if not leaked
            else f"Sent {_words(leaked)} to the provider."
        )
    restored = derived.get("restored")
    if restored == "all":
        parts.append("Handed every value back to the client.")
    elif restored == "none":
        parts.append("Did not give the caller their own data back.")
    if "leakSplitN" in derived and "leakWholeN" in derived:
        if derived["leakSplitN"] > derived["leakWholeN"]:
            parts.append("Splitting a value across two chunks leaked more of them.")
        elif derived["leakSplitN"] == derived["leakWholeN"]:
            parts.append("Splitting a value changed nothing.")
    return " ".join(parts) or "Submitted without a report this check could read."


# ------------------------------------------------------------------------ the row


def validate(fields: dict[str, str]) -> list[str]:
    """Everything wrong with a submission, in one pass.

    All of it at once, never the first problem only. A submitter who is sent back three
    times for three fields usually stops submitting.
    """
    problems: list[str] = []
    for name in REQUIRED_FIELDS:
        if not fields.get(name, "").strip():
            heading = next(h for h, f in FIELD_BY_HEADING.items() if f == name)
            problems.append(f'The "{heading}" section is empty, and a row cannot be written without it.')
    if fields.get("architecture") and normalize_architecture(fields["architecture"]) is None:
        problems.append(
            'The "how it reads the stream" answer is not one of the offered options: '
            + ", ".join(ARCHITECTURES)
            + "."
        )
    if fields.get("run_url") and not RUN_URL.match(fields["run_url"].strip()):
        problems.append(
            "The run link is not a GitHub Actions run URL. It should look like "
            "`https://github.com/OWNER/REPO/actions/runs/123456789`, and the run has to be "
            "public: the report is read from its artifact rather than from this issue."
        )
    return problems


def build_row(
    fields: dict[str, str],
    provenance: str,
    derived: dict[str, Any],
    *,
    issue_number: int,
    submitter: str,
    date: str,
    evidence: str,
) -> dict[str, Any]:
    """The row, complete when the artifact was readable and a draft when it was not."""
    measured = {key: value for key, value in derived.items() if not key.startswith("_")}
    publishable = "sent" in measured and "restored" in measured
    row: dict[str, Any] = {
        "status": "published" if publishable else "draft",
        "date": date,
        "project": clean(fields.get("gateway", ""), limit=80),
        "version": clean(fields.get("version", ""), limit=120),
        **measured,
        "note": clean(write_note(derived), limit=300),
        "provenance": provenance,
        "architecture": normalize_architecture(fields.get("architecture", "")) or "not-stated",
        "license": clean(fields.get("license", ""), limit=40),
    }
    run_url = clean(fields.get("run_url", ""), limit=300)
    if RUN_URL.match(run_url):
        row["runUrl"] = run_url
    project_url = clean(fields.get("project_url", ""), limit=300)
    if project_url.startswith(("https://", "http://")):
        row["pricingUrl"] = project_url
    # The repository the run actually happened in, recorded separately from the name the
    # submitter gave the gateway. Nothing stops somebody pointing at another project's
    # genuine run and labelling it as their own product; the run URL is on the row, so
    # that has always been checkable, but only by clicking. Recording the repository makes
    # a mismatch between "who ran it" and "what it is called" visible without leaving the
    # page, which is the cheapest defence against misattribution and costs an honest
    # submitter nothing.
    run_match = RUN_URL.match(run_url)
    row["_submission"] = {
        "issue": issue_number,
        "submitter": submitter,
        "ranIn": f"{run_match.group(1)}/{run_match.group(2)}" if run_match else "",
        "evidence": evidence,
        "notes": clean(fields.get("notes", ""), limit=600),
    }
    return row


def render_comment(row: dict[str, Any], reason: str, evidence: str, problems: list[str]) -> str:
    """What the workflow posts back on the issue."""
    if problems:
        lines = ["Thanks for sending this. A few things are missing before it can become a row:", ""]
        lines += [f"- {problem}" for problem in problems]
        lines += ["", "Edit the issue and this check runs again. Nothing is lost."]
        return "\n".join(lines)

    published = row.get("status") == "published"
    body = json.dumps(row, indent=2, ensure_ascii=False)
    lines = [
        "**Published.** Your row is on the [results wall](https://llmshieldproxy.com/docs/conformance/who-has-run-it)."
        if published
        else "**Not published yet.** The submission parsed, but no report could be read from the run.",
        "",
        f"**Who ran it: `{row['provenance']}`.** {reason}",
        "",
        evidence,
        "",
    ]
    if published:
        lines += [
            "Every number in the row was read out of that run's own artifact, not from this "
            "issue. What that establishes is where the run happened and what it recorded, "
            "not that the gateway was the version named: nothing outside your repository "
            "can establish that.",
            "",
        ]
        if "leakWholeN" not in row:
            lines += [
                "The two response-split columns say `not measured`, which is accurate: they "
                "come from a profile that injects values into the response, and the CI check "
                "measures what your gateway sends upstream. Nothing is assumed in their "
                "place.",
                "",
            ]
    else:
        lines += [
            "Link a public Actions run whose artifact holds `current.json` and this check "
            "will fill the row in by itself. Until then the row is parked and nothing about "
            "it is on the page.",
            "",
        ]
    lines += ["<details><summary><b>The row</b></summary>", "", "```json", body, "```", "", "</details>", ""]
    lines += [
        "Anything wrong with it is a bug in this check rather than in your result. Say so "
        "here and it gets fixed."
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- writing


def row_identity(row: dict[str, Any]) -> tuple[str, ...]:
    """What makes two rows the same row, for replacement rather than accumulation.

    THE RUN IS THE IDENTITY when there is one. Keying on the issue number alone was the
    obvious choice and it was wrong: issue numbers are free, so the same run URL submitted
    from a hundred issues produced a hundred identical rows, each one passing every check
    because each one WAS a genuine verified result. One run is one measurement however
    many times it is posted.

    Without a run, a project and version is the next best thing, so a project correcting
    its own row replaces it rather than appearing twice.
    """
    run_url = str(row.get("runUrl") or "")
    if run_url:
        return ("run", run_url)
    return ("target", str(row.get("project", "")).casefold(), str(row.get("version", "")).casefold())


def submissions_by(entries: list[Any], submitter: str) -> int:
    return sum(
        1
        for entry in entries
        if isinstance(entry, dict) and (entry.get("_submission") or {}).get("submitter") == submitter
    )


def append_row(row: dict[str, Any], path: Path = ROWS_FILE) -> None:
    """Append to the rows file, keeping it valid JSON at every step.

    Read, parse, replace-or-append, dump. A malformed result fails here, in a workflow
    log, rather than at the next site build.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    entries = document.setdefault("entries", [])
    if not isinstance(entries, list):
        raise ValueError("submitted-rows.json: 'entries' is not a list")
    identity = row_identity(row)
    document["entries"] = [
        existing
        for existing in entries
        if not (isinstance(existing, dict) and row_identity(existing) == identity)
    ]
    document["entries"].append(row)
    # LF and a trailing newline, matching `write_json_artifact` in the benchmark: a CRLF
    # file here breaks hash identity across platforms for no gain.
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )


def _run(*command: str, cwd: Optional[Path] = None) -> None:
    subprocess.run(command, check=True, cwd=cwd or REPO_ROOT)  # nosec B603 - fixed argument lists


def build_site() -> None:
    """Run the real site build, which is the check a pull request here would not get.

    A PR opened with GITHUB_TOKEN triggers no workflows, and `deploy-docs.yml` builds on
    push to main rather than on a pull request. So the build that would have caught a bad
    row after the merge is run here, before the commit, where it can still stop it.
    """
    _run("npm", "ci", "--no-audit", "--no-fund", cwd=WEBSITE)
    _run("npm", "run", "build", cwd=WEBSITE)


def publish(row: dict[str, Any], issue_number: int) -> None:
    """Land the row through a pull request, which is what the branch rule asks for.

    THROUGH A PR, NOT AROUND ONE. `main` requires changes to arrive via a pull request,
    and the first version of this pushed straight at it and was refused: GH006, protected
    branch hook declined. The obvious fixes were all worse than the rule. A bypass list
    cannot express "the bot but not me" on a user-owned repository, because the only
    bypass actors offered there are roles and roles are hierarchical, so exempting Write
    exempts Admin too. A deploy key or a token would express it, at the cost of a
    write-capable credential sitting in secrets for any workflow to pick up.

    None of that is needed. The rule requires a pull request; it does not require anyone
    to approve one. Measured on this repository: zero approvals, no required checks, no
    last-push approval. So a pull request from this job is immediately mergeable, and
    opening and merging one satisfies the rule rather than evading it. Nothing gains a
    bypass, no credential is stored, and every published row leaves a reviewable PR behind
    instead of a bare commit.

    WHAT THIS DOES NOT GET. A pull request opened with GITHUB_TOKEN starts no workflows, so
    the reviewers do not run on it. That is the right trade only because of what the diff
    can contain: the guard below means one JSON data file or nothing. Were this ever to
    carry a code change, the absence of review would matter and this comment would be
    wrong.

    The branch, commit and PR name no agent, model or provider, which is a repository rule
    and applies to text a workflow generates too.
    """
    _run("git", "config", "user.name", "github-actions[bot]")
    _run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    _run("git", "add", str(ROWS_FILE.relative_to(REPO_ROOT)))

    # THE ONLY FILE THIS JOB MAY EVER CHANGE, checked rather than intended.
    #
    # This workflow is triggered by issues, which anyone can open, so an outsider can
    # cause a run of it that holds a token able to write to the default branch. Every
    # other control here is an argument that the run cannot be steered: the body never
    # reaches a shell, no submitted code executes, only JSON is parsed. This one is not an
    # argument. It reads back what is actually staged and refuses to push anything but the
    # rows file, so a bug anywhere upstream of it cannot become a commit to main.
    staged = subprocess.run(  # nosec B603 B607 - fixed argument list
        ["git", "diff", "--cached", "--name-only"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    allowed = {ROWS_FILE.relative_to(REPO_ROOT).as_posix()}
    if set(staged) - allowed:
        raise RuntimeError(
            "refusing to publish: this job may only change "
            f"{sorted(allowed)}, and it staged {sorted(staged)}"
        )

    title = f"feat(results-wall): add {row['project']} {row['version']} (#{issue_number})"
    _run("git", "commit", "-m", title)

    # Rebase before pushing the branch. The workflow serialises its own runs, but main
    # still moves underneath a job that has been building for two minutes, and a branch
    # cut from a stale main makes a PR with an unrelated diff in it.
    _run("git", "pull", "--rebase", "origin", "main")
    branch = f"intake/issue-{issue_number}"
    _run("git", "push", "--set-upstream", "origin", f"HEAD:{branch}")

    body = (
        f"Automated from #{issue_number}.\n\n"
        f"Every measured column was read from the artifact of the run linked in that "
        f"issue, not from anything typed in it. Provenance: `{row['provenance']}`.\n\n"
        f"The site was built before this branch was pushed, so the row is known not to "
        f"break it. This pull request may only ever contain "
        f"`{ROWS_FILE.relative_to(REPO_ROOT).as_posix()}`; the job refuses to push if "
        f"anything else is staged.\n\n"
        f"Closes #{issue_number}\n"
    )
    try:
        _run("gh", "pr", "create", "--base", "main", "--head", branch, "--title", title, "--body", body)
        # Squash, so one row is one commit on main whatever the branch looks like, and
        # delete the branch behind it: these accumulate one per submission otherwise.
        _run("gh", "pr", "merge", branch, "--squash", "--delete-branch")
    except (OSError, subprocess.SubprocessError):
        # Take the branch back down before giving up. A failure between pushing it and
        # merging it used to leave `intake/issue-N` behind on the remote, so a retry hit a
        # branch that already existed and the repository slowly filled with dead ones.
        _try("git", "push", "origin", "--delete", branch)
        raise


def _try(*command: str) -> bool:
    """Run a courtesy that must never cost a row.

    Commenting, labelling and closing all happen AFTER the row is committed and the site
    deployed. By then the submitter's result is live, and failing the job over a label
    that does not exist would report the whole submission as broken when the only thing
    that broke was the thank-you note. The failure is printed and the job carries on.
    """
    finished = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=False)  # nosec B603
    if finished.returncode != 0:
        print(f"Could not {' '.join(command[:3])}: {(finished.stderr or '').strip()[:200]}", file=sys.stderr)
    return finished.returncode == 0


def comment(issue_number: int, text: str) -> bool:
    return _try("gh", "issue", "comment", str(issue_number), "--body", text)


def label(issue_number: int, name: str) -> bool:
    return _try("gh", "issue", "edit", str(issue_number), "--add-label", name)


def close_issue(issue_number: int) -> bool:
    return _try("gh", "issue", "close", str(issue_number), "--reason", "completed")


# ------------------------------------------------------------------------------ main


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--event-path", default=os.getenv("GITHUB_EVENT_PATH"))
    parser.add_argument("--dry-run", action="store_true", help="Print what would happen; touch nothing.")
    args = parser.parse_args(argv)

    if not args.event_path or not Path(args.event_path).is_file():
        print("No event payload to read.", file=sys.stderr)
        return 2
    event = json.loads(Path(args.event_path).read_text(encoding="utf-8"))
    issue = event.get("issue") or {}
    issue_number = int(issue.get("number") or 0)
    if not issue_number:
        print("Event carries no issue number.", file=sys.stderr)
        return 2

    fields = parse_submission(issue.get("body") or "")
    problems = validate(fields)
    row: dict[str, Any] = {}
    reason = evidence = ""
    if not problems:
        provenance, reason, where = classify_provenance(fields.get("run_url", ""))
        reports, evidence = collect_reports(*where) if where else ({}, "No run to read a report from.")
        row = build_row(
            fields,
            provenance,
            derive_measurements(reports),
            issue_number=issue_number,
            submitter=str((issue.get("user") or {}).get("login") or "unknown"),
            # The issue's own creation date, not today's. A submission that sat in the
            # queue for a week is not a measurement taken a week later.
            date=(str(issue.get("created_at") or "")[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            evidence=evidence,
        )

    text = render_comment(row, reason, evidence, problems)
    if args.dry_run:
        print(text)
        if row:
            print("\n--- row ---")
            print(json.dumps(row, indent=2, ensure_ascii=False))
        return 1 if problems else 0

    if problems:
        comment(issue_number, text)
        label(issue_number, "needs-info")
        return 1

    # A row that could not be filled from an artifact is never written to the file. It
    # would not be committed either way, so writing it would leave the workspace dirty and
    # the row nowhere; the comment tells the submitter exactly what to link instead.
    if row.get("status") != "published":
        comment(issue_number, text)
        label(issue_number, "needs-info")
        return 1

    # The cap is checked against the rows that already exist, and a REPLACEMENT is always
    # allowed: somebody correcting or reposting a row they already hold is not adding one.
    existing = json.loads(ROWS_FILE.read_text(encoding="utf-8")).get("entries", [])
    submitter = (row.get("_submission") or {}).get("submitter", "")
    replacing = any(
        isinstance(entry, dict) and row_identity(entry) == row_identity(row) for entry in existing
    )
    if not replacing and submissions_by(existing, submitter) >= MAX_ROWS_PER_SUBMITTER:
        comment(
            issue_number,
            f"This account already holds {MAX_ROWS_PER_SUBMITTER} rows on the wall, which "
            "is the cap, so this one was not published. That is a flood guard rather than "
            "a judgement: if you have a real reason to need more, say so here and it will "
            "be raised. Resubmitting a run that already has a row still works and replaces "
            "it.",
        )
        label(issue_number, "needs-info")
        return 1

    append_row(row)
    # Build BEFORE the commit, never after. This is the only place the site build runs on
    # this path, so a row that breaks it has to fail here or it reaches the deploy.
    build_site()
    try:
        publish(row, issue_number)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        # Say so on the issue rather than only in a log the submitter cannot see. A run
        # that parsed their result, verified it and built the site, and then could not
        # write it, is our problem and not theirs: the first time this happened the job
        # went red and the issue stayed silent.
        comment(
            issue_number,
            "This result was read and verified, and then could not be published "
            f"({type(exc).__name__}). Nothing is wrong with your submission and nothing "
            "needs redoing. The failure is on this side and someone will pick it up.",
        )
        label(issue_number, "needs-info")
        print(f"Publishing failed after a successful build: {exc}", file=sys.stderr)
        return 1
    comment(issue_number, text)
    close_issue(issue_number)
    return 0


if __name__ == "__main__":
    sys.exit(main())
