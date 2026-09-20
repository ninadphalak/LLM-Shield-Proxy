"""Turn a measured failure into a bug ticket detailing what leaked, who saw it, and why.

This module formats results determined by `http_profile` and `v2_emitter` into
actionable reports explaining leak directions (client vs. provider) and fidelity failures.
It strictly adheres to privacy rules by omitting generated values from published reports
while displaying them in the operator terminal. Standard library only.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

KIND_LEAK_TO_CLIENT = "leak-to-client"
KIND_LEAK_TO_PROVIDER = "leak-to-provider"
KIND_FIDELITY = "fidelity"

KINDS = (KIND_LEAK_TO_CLIENT, KIND_LEAK_TO_PROVIDER, KIND_FIDELITY)

# Minimum value column width. True width is sized per block so notes align properly.
_MIN_VALUE_COLUMN = 20
# Maximum length for the target's returned text before truncation.
_MAX_RECEIVED = 60


@dataclass(frozen=True)
class Labels:
    """Who wrote it, who saw it, and a note on each side.

    ``received_note`` is the arrow: exactly one line in a block carries it, and it is
    always the line that should have read differently.
    """

    sent: str
    received: str
    sent_note: str
    received_note: str


_LABELS = {
    # Model-invented values that reached the client unstripped.
    KIND_LEAK_TO_CLIENT: Labels(
        "the model wrote:",
        "the user saw:",
        "(nobody sent this to you)",
        "<- the gateway should have removed it",
    ),
    # Caller values that reached the provider unredacted.
    KIND_LEAK_TO_PROVIDER: Labels(
        "you sent:",
        "the provider saw:",
        "",
        "<- nothing removed it on the way",
    ),
    KIND_FIDELITY: Labels(
        "you sent:",
        "the user saw:",
        "",
        "<- your own value never came back",
    ),
}

# Distinct headlines for unattributed findings (traffic on public captures lacking run markers).
_UNATTRIBUTED_HEADLINE = "LEAK  {entity} reached the capture, source unproven"
_UNATTRIBUTED_SENTENCE = (
    "A request carrying the caller's own {phrase} reached the capture without this "
    "run's marker. It cannot be attributed to the gateway under test."
)

_HEADLINES = {
    KIND_LEAK_TO_CLIENT: "LEAK  {entity} reached the client",
    KIND_LEAK_TO_PROVIDER: "LEAK  {entity} reached the model provider",
    # FAIL, not LEAK. Indicates missing data rather than exposed data.
    KIND_FIDELITY: "FAIL  {entity} was not restored",
}


# Used only when target's redaction feature was confirmed active for the run.
_REDACTION_WAS_ON_NOTE = "<- redaction was on and did not hold"


def labels_for(kind: str, *, redaction_configured: bool = False) -> Labels:
    labels = _LABELS[kind]
    if redaction_configured and kind == KIND_LEAK_TO_PROVIDER:
        return Labels(labels.sent, labels.received, labels.sent_note, _REDACTION_WAS_ON_NOTE)
    return labels


# --------------------------------------------------------------------------- the words
#
# CANNED EXPLANATIONS. Sentences are assembled from fixed templates rather than
# generated prose, combining what happened (kind/split) with why it matters (entity type).
# Carrier is excluded from keys because it dictates where to look, not the nature of the fix.

_WHAT_HAPPENED = {
    (KIND_LEAK_TO_CLIENT, False): (
        "A gateway is meant to strip data the caller never supplied. This {phrase} came "
        "from the model and went through to the user unchanged."
    ),
    (KIND_LEAK_TO_CLIENT, True): (
        "The {phrase} arrived from the model split across two events. Neither half "
        "matched anything on its own, so the gateway forwarded both, and the client "
        "joined them back together."
    ),
    # Redaction configuration status is appended separately if confirmed active.
    (KIND_LEAK_TO_PROVIDER, False): (
        "The gateway forwarded the caller's own {phrase} to the model provider byte for "
        "byte. Not doing that is a privacy gateway's one job on the request path."
    ),
    (KIND_LEAK_TO_PROVIDER, True): (
        "The gateway forwarded the caller's own {phrase} to the model provider in pieces. "
        "No single piece matched a detector; joining what the gateway sent and stripping "
        "the separators gives the value back whole."
    ),
    (KIND_FIDELITY, False): (
        "The caller's own {phrase} was not returned. Nothing escaped, so this is not a "
        "leak: the response is missing data the caller supplied, which is the opposite "
        "failure and needs a different fix."
    ),
    (KIND_FIDELITY, True): (
        "The caller's own {phrase} came back in pieces that do not reassemble. Nothing "
        "escaped, so this is not a leak: joining the response gives something other than "
        "what was sent, which points at the retention window rather than at a detector."
    ),
}

# Distinct rationales explaining why specific entity leaks matter.
_WHY_IT_MATTERS = {
    "EMAIL": (
        "An address is the cheapest key there is for tying a transcript to a named person."
    ),
    "SSN": (
        "A Social Security number is a government identifier and cannot be rotated after "
        "it is exposed."
    ),
    "CREDIT_CARD": (
        "Under PCI DSS a primary account number is exactly the value a gateway exists to "
        "stop."
    ),
    "USPHONE": (
        "A phone number is a direct contact channel and a common account-recovery factor."
    ),
    "AWS_ACCESS_KEY_ID": (
        "An access key ID names the account half of an AWS credential and tells an "
        "attacker which secret is worth hunting for."
    ),
    # Suppress B105 (hardcoded token check) as these are descriptions, not actual secrets.
    "GITHUB_TOKEN": (  # nosec B105 -- an explanation, not a token
        "A GitHub token is a bearer credential: whoever holds it is the user it was "
        "issued to."
    ),
    "SLACK_TOKEN": (  # nosec B105 -- an explanation, not a token
        "A Slack bot token can read and post in every channel its app was installed in."
    ),
}

KNOWN_ENTITY_TYPES = tuple(sorted(_WHY_IT_MATTERS))

_ENTITY_PHRASE = {
    "EMAIL": "email address",
    "SSN": "US Social Security number",
    "CREDIT_CARD": "payment card number",
    "USPHONE": "phone number",
    "AWS_ACCESS_KEY_ID": "AWS access key ID",
    "GITHUB_TOKEN": "GitHub access token",  # nosec B105 -- a label, not a token
    "SLACK_TOKEN": "Slack bot token",  # nosec B105 -- a label, not a token
}


# Fallback label for findings lacking an entity (e.g. CI runs without specimens).
_UNNAMED_ENTITY_PHRASE = "protected value"


def entity_phrase(entity_type: str) -> str:
    """What a reader calls this type. ``CREDIT_CARD`` is a corpus label, not English."""
    return (
        _ENTITY_PHRASE.get(entity_type)
        or entity_type.replace("_", " ").lower()
        or _UNNAMED_ENTITY_PHRASE
    )


# Appended only if the redaction feature was explicitly recorded as active.
_REDACTION_ON_CLAUSE = (
    "The target's redaction feature was recorded as switched on for this run."
)


def explanation(
    kind: str,
    entity_type: str,
    *,
    fragmented: bool = False,
    redaction_configured: bool = False,
    attributed: bool = True,
) -> str:
    """Return the canned explanation for a finding. Unknown entities degrade gracefully."""
    phrase = entity_phrase(entity_type)
    if not attributed and kind == KIND_LEAK_TO_PROVIDER:
        # Omit redaction claims for unattributed traffic.
        matters = _WHY_IT_MATTERS.get(entity_type, "")
        sentence = _UNATTRIBUTED_SENTENCE.format(phrase=phrase)
        return f"{sentence} {matters}".strip() if matters else sentence
    parts = [_WHAT_HAPPENED[(kind, bool(fragmented))].format(phrase=phrase)]
    if redaction_configured and kind == KIND_LEAK_TO_PROVIDER:
        parts.append(_REDACTION_ON_CLAUSE)
    matters = _WHY_IT_MATTERS.get(entity_type, "")
    if matters:
        parts.append(matters)
    return " ".join(parts)


# --------------------------------------------------------------------------- findings


@dataclass(frozen=True)
class Finding:
    """One failure addressed to a reader. Specimens (`sent`/`received`) are omitted from publications."""

    kind: str
    entity_type: str
    carrier: str
    encoding: str = "plain"
    fragmented: bool = False
    sent: str = ""
    received: str = ""
    seed: str = ""
    # Whether target redaction was explicitly recorded as enabled.
    redaction_configured: bool = False
    # True if the request carried this run's marker. False means unattributed public capture traffic.
    attributed: bool = True


@dataclass
class OperatorSpecimens:
    """Synthetic values generated for this run. Kept separate from reports for privacy."""

    fixture: dict[str, str] = field(default_factory=dict)
    prompt: str = ""
    client_text: str = ""
    seed: str = ""


# Map internal harness channel names to reader-friendly terms.
_CARRIER_FOR_CHANNEL = {
    "body": "chat-content",
    "headers": "http-header",
    "framing": "chunk-framing",
    "request": "request-line",
    # Represents the combined per-request pass over everything sent upstream.
    "all": "upstream-request",
}


def findings_from_report(
    report: dict[str, Any],
    specimens: Optional[OperatorSpecimens] = None,
    *,
    seed: str = "",
    duty: str = "restore",
) -> list[Finding]:
    """Parse an HTTP-profile report into display findings. Requires no measurement logic.
    Omitting `specimens` produces safe, value-free rows for published reports.

    The `duty` parameter (`restore` or `anonymize`) dictates whether missing fidelity
    is treated as a failure or correct anonymization.
    """
    if duty not in {"restore", "anonymize"}:
        raise ValueError("duty must be restore or anonymize")
    boundary = report["checks"]["configured_upstream_boundary"]
    fixture = specimens.fixture if specimens else {}
    # Ensure seed is present for repeatability. Supplied by caller for published runs.
    seed = (specimens.seed if specimens else "") or seed
    # True only if explicitly recorded, never assumed active.
    configured = bool(
        report.get("redaction_claim", {}).get("configured_for_this_run", False)
    )

    findings: list[Finding] = []
    seen: set[str] = set()
    # Keep attributed and unattributed leaks separate to preserve marker distinction.
    # Attributed first, so a real finding is never buried under someone else's traffic.
    evidence: Iterable[tuple[dict[str, Any], bool]] = [
        *((item, True) for item in boundary.get("leak_evidence", [])),
        *((item, False) for item in boundary.get("unattributed_leak_evidence", [])),
    ]
    for item, attributed in evidence:
        entity = str(item.get("entity_type", ""))
        if not entity or entity in seen:
            continue
        seen.add(entity)
        value = fixture.get(entity, "")
        findings.append(
            Finding(
                kind=KIND_LEAK_TO_PROVIDER,
                entity_type=entity,
                carrier=_CARRIER_FOR_CHANNEL.get(
                    str(item.get("channel", "")), "upstream-request"
                ),
                # `normalized` indicates a fragmented value recovered after stripping separators.
                fragmented=item.get("match") == "normalized",
                sent=value,
                received=value,
                seed=seed,
                redaction_configured=configured and attributed,
                attributed=attributed,
            )
        )

    fidelity = report["checks"].get("response_fidelity", {})
    # Skip fidelity findings if the duty is 'anonymize', as dropping values is expected.
    if duty != "anonymize" and not fidelity.get("passed", True):
        findings.extend(_fidelity_findings(fixture, specimens, seed))
    return findings


def _fidelity_findings(
    fixture: dict[str, str], specimens: Optional[OperatorSpecimens], seed: str
) -> list[Finding]:
    """Generate findings only for values that failed to return.
    If no specimens are provided, reports a generic unattributed failure.
    """
    if not specimens or not fixture:
        return [
            Finding(
                kind=KIND_FIDELITY,
                entity_type="",
                carrier="chat-content",
                seed=seed,
            )
        ]
    rows = []
    for entity, value in sorted(fixture.items()):
        if value and value in specimens.client_text:
            continue  # restored correctly; this entity is not the failure
        rows.append(
            Finding(
                kind=KIND_FIDELITY,
                entity_type=entity,
                carrier="chat-content",
                sent=value,
                received=substituted_text(specimens.prompt, specimens.client_text, value),
                seed=seed,
            )
        )
    return rows


def substituted_text(prompt: str, response: str, value: str) -> str:
    """Extract the text received by the client in place of the original `value`.
    Empty returns mean nothing replaced it. Aligning texts exposes arbitrary target placeholders.
    """
    if not value or not prompt:
        return ""
    start = prompt.find(value)
    if start < 0:
        return ""
    end = start + len(value)
    pieces: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, prompt, response, autojunk=False
    ).get_opcodes():
        if i2 <= start or i1 >= end:
            continue
        if tag == "equal":
            # Clip verbatim matches to the fragment boundary.
            low, high = max(i1, start), min(i2, end)
            pieces.append(response[j1 + (low - i1) : j1 + (high - i1)])
        else:
            pieces.append(response[j1:j2])
    return "".join(pieces).strip()


# --------------------------------------------------------------------------- rendering


def render(finding: Finding, *, reveal: bool) -> list[str]:
    """Render finding as text lines. `reveal=False` enforces privacy for published artifacts."""
    unattributed = not finding.attributed and finding.kind == KIND_LEAK_TO_PROVIDER
    labels = labels_for(
        finding.kind,
        redaction_configured=finding.redaction_configured and not unattributed,
    )
    if unattributed:
        labels = Labels(
            labels.sent, "the capture saw:", labels.sent_note, "<- from an unknown sender"
        )
    entity = finding.entity_type or "A protected value"
    headline = _UNATTRIBUTED_HEADLINE if unattributed else _HEADLINES[finding.kind]
    lines = [headline.format(entity=entity)]

    if reveal:
        sent = finding.sent or "(not recorded)"
        received = _clip(finding.received) or "(nothing came back in its place)"
    else:
        shape = f"<{finding.entity_type}>" if finding.entity_type else "<VALUE>"
        sent = shape
        # Represent values with entity shapes. Fidelity failures hide target-provided text.
        received = shape if finding.kind != KIND_FIDELITY else "<not the value you sent>"

    width = max(len(sent), len(received), _MIN_VALUE_COLUMN) + 2
    lines.append(_value_line(labels.sent, sent, labels.sent_note, width))
    lines.append(_value_line(labels.received, received, labels.received_note, width))
    lines.extend(
        f"      {line}"
        for line in _wrap(
            explanation(
                finding.kind,
                finding.entity_type,
                fragmented=finding.fragmented,
                redaction_configured=finding.redaction_configured,
                attributed=finding.attributed,
            ),
            72,
        )
    )
    lines.append(
        f"      Case: {finding.carrier} / {finding.encoding} / "
        f"split={'yes' if finding.fragmented else 'no'}   "
        f"Seed: {_safe_seed(finding.seed) or _NO_SEED}"
    )
    return lines


# Short label for unseeded runs.
_NO_SEED = "none"


def _safe_seed(seed: str) -> str:
    """Strip newlines and backticks from user-provided seeds to prevent Markdown breakouts."""
    return " ".join(seed.replace("`", "").split())


def _value_line(label: str, value: str, note: str, width: int) -> str:
    left = f"      {label:<18}{value}"
    if not note:
        return left.rstrip()
    return f"{left.ljust(6 + 18 + width)}{note}".rstrip()


def _clip(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= _MAX_RECEIVED else collapsed[: _MAX_RECEIVED - 3] + "..."


def _wrap(text: str, width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def published_dict(finding: Finding) -> dict[str, Any]:
    """Format row for artifact publication. Ensures safe extraction without specimens."""
    return {
        "kind": finding.kind,
        "entity_type": finding.entity_type,
        "carrier": finding.carrier,
        "encoding": finding.encoding,
        "fragmented": finding.fragmented,
        "seed": finding.seed,
        "redaction_configured": finding.redaction_configured,
        "attributed": finding.attributed,
        "explanation": explanation(
            finding.kind,
            finding.entity_type,
            fragmented=finding.fragmented,
            redaction_configured=finding.redaction_configured,
            attributed=finding.attributed,
        ),
        "display": render(finding, reveal=False),
        "values_published": False,
    }


def print_findings(findings: list[Finding], *, reveal: bool) -> None:
    """The operator's section. Prints nothing at all when nothing failed."""
    if not findings:
        return
    print("  What leaked, and why it matters")
    print()
    for finding in findings:
        for line in render(finding, reveal=reveal):
            print(line)
        print()
    if reveal:
        print("    The values above are this run's own synthetic specimens, printed here")
        print("    and deliberately left out of the JSON report (values_published: false).")
    if any(not finding.seed for finding in findings):
        print("    Seed 'none' means this run generated fresh values and the next run will")
        print("    use different ones. `pii-leak-benchmark ci --seed <name>` fixes them so")
        print("    a finding reproduces exactly.")
    print()
