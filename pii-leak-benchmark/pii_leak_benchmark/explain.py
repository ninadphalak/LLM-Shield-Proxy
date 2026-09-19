"""Turn a measured failure into a bug ticket: what left, who saw it, and why it matters.

WHY THIS EXISTS. A run reports ``leak_rate: 0.125``. That is correct, precise, and gives
the person reading it no next action. It does not say WHAT left, WHICH side of the gateway
saw it, or WHAT to change. The difference between "0.125" and "your gateway handed the
caller's Social Security number to its model provider byte for byte" is the difference
between a report and a bug ticket -- and it is the difference between persuading someone
who already believes the problem is real and persuading someone who does not.

Nothing here measures anything. Every value this module prints was decided by the scorers
in ``http_profile`` and ``v2_emitter``; this is the layer that says it out loud. That
separation is load-bearing, not tidiness: the IEEE submission is pinned to inspector
``94262e29a492ab6a``, which digests the scorers and only the scorers, so an output layer
cannot move a published number. Keep measurement out of this file and that stays true.

THREE FAILURES, THREE PAIRS OF PEOPLE. A gateway can fail in three unrelated directions,
and an operator who confuses them fixes the wrong subsystem:

    leak-to-client     a value the MODEL invented reached the user, unstripped
    leak-to-provider   the CALLER's own value reached the model provider, unredacted
    fidelity           the CALLER's own value never came back

The first two are leaks in opposite directions. The third is not a leak at all -- nothing
escaped -- and reporting it as one sends someone to audit a redactor that is working.

SAY ONLY WHAT THE RUN ESTABLISHED. Two facts the display must not assume, because both
were assumed in the first draft and both read fine on the page:

- A run may say "redaction was on and did not hold" only if it RECORDED
  ``redaction_claim.configured_for_this_run``. The documented ``capture://self`` floor has
  no gateway to accuse, and it is the first run an operator is told to do.
- A leak found in ``unattributed_leak_evidence`` is traffic that reached a PUBLIC capture
  without this run's marker. ``http_profile`` keeps it in its own field so a reader is not
  told the target sent it; the display keeps the same separation, with its own headline,
  label, arrow and sentence.

WHY TWO LINES AND NOT ONE. In a leak the two values are IDENTICAL, and that identity is
the finding: the gateway held the value, was asked to change it, and returned it byte for
byte. Printing "sent" beside "received" without saying who is who therefore explains
nothing; it reads as a successful round trip. The labels carry the meaning, because the
values cannot.

THE PRIVACY RULE, WHICH IS ABOUT THE ARTIFACT AND NOT ABOUT THE TERMINAL. Corpus values
are generated per run and ``values_published`` is false. That governs the PUBLISHED REPORT.
The operator generated these values on their own machine seconds earlier, so showing them
their own test data discloses nothing they did not just create. So: print to the operator,
omit from the report. ``render(..., reveal=False)`` is a pure function of the case
identity -- it does not read ``sent`` or ``received`` at all, so there is no input a later
edit can smuggle a specimen through, and ``<EMAIL>`` carries the shape instead.

Standard library only. This module is imported by the neutral harness, which depends on
``httpx`` and nothing else.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

KIND_LEAK_TO_CLIENT = "leak-to-client"
KIND_LEAK_TO_PROVIDER = "leak-to-provider"
KIND_FIDELITY = "fidelity"

KINDS = (KIND_LEAK_TO_CLIENT, KIND_LEAK_TO_PROVIDER, KIND_FIDELITY)

# The narrowest the value column is allowed to get. The real width is sized per block
# from the two values in it, so the notes on the two lines line up with each other and
# the gutter stays small: a 40-character GitHub token and an 11-character SSN pushed to a
# single constant width put one arrow half a screen from the value it refers to.
_MIN_VALUE_COLUMN = 20
# What the gateway returned instead is the TARGET's own text and can be any length, so it
# is the one string that needs a ceiling before it reaches a terminal.
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
    # The model invented a value and it went straight to the user. "you sent" would be a
    # lie here -- nobody sent this; that is the entire point, and the note says so.
    KIND_LEAK_TO_CLIENT: Labels(
        "the model wrote:",
        "the user saw:",
        "(nobody sent this to you)",
        "<- the gateway should have removed it",
    ),
    # Two notes, and the harness must earn the stronger one. "redaction was on and did
    # not hold" is an accusation about a CONFIGURATION, and on the documented no-gateway
    # floor there is no gateway to accuse: the run that must report LEAK would have been
    # printing a false sentence about a product that was never in the path. The weaker
    # note is true in every case, states only what was observed, and still marks the line.
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

# An UNATTRIBUTED finding gets its own headline and its own sentence, because a public
# capture is reachable by anyone and receives traffic that is not the target's.
# `http_profile` keeps those in separate fields precisely "so a reader is not told the
# target sent them", and the display has to keep that separation or it undoes it: someone
# who knows the fixture and the capture URL could otherwise manufacture an accusation.
_UNATTRIBUTED_HEADLINE = "LEAK  {entity} reached the capture, source unproven"
_UNATTRIBUTED_SENTENCE = (
    "A request carrying the caller's own {phrase} reached the capture, but it did not "
    "carry this run's marker, so it cannot be attributed to the gateway under test. A "
    "public capture is reachable by anyone. Treat this as something to explain, not as a "
    "finding about the target."
)

_HEADLINES = {
    KIND_LEAK_TO_CLIENT: "LEAK  {entity} reached the client",
    KIND_LEAK_TO_PROVIDER: "LEAK  {entity} reached the model provider",
    # FAIL, not LEAK. Nothing escaped, and the word is what stops an operator opening a
    # privacy incident over a broken response path.
    KIND_FIDELITY: "FAIL  {entity} was not restored",
}


# Used only when the run RECORDED that the target's redaction feature was switched on for
# it. Then the product was configured to prevent exactly this and did not, which is a
# stronger and more useful statement than "nothing removed it".
_REDACTION_WAS_ON_NOTE = "<- redaction was on and did not hold"


def labels_for(kind: str, *, redaction_configured: bool = False) -> Labels:
    labels = _LABELS[kind]
    if redaction_configured and kind == KIND_LEAK_TO_PROVIDER:
        return Labels(labels.sent, labels.received, labels.sent_note, _REDACTION_WAS_ON_NOTE)
    return labels


# --------------------------------------------------------------------------- the words
#
# CANNED, NOT GENERATED. Fixed strings chosen by a key. The table's only job is to
# distinguish cases, so `test_no_two_canned_explanations_are_the_same` sweeps the whole key
# space: a row that collapses onto another stops distinguishing anything, and it is always
# the row added last.
#
# A sentence is composed from two fixed halves -- what happened (by kind and whether the
# value was split) and why this type matters (by entity). Both halves are literals and the
# join rule is fixed, so the result is still a template lookup and not generated prose; the
# composition exists so adding one entity does not mean writing eighteen new sentences that
# then drift apart.
#
# `carrier` is deliberately NOT part of the key. The doc's sketch keys on it, but in this
# corpus the carrier does not change what a reader must do about a finding -- it changes
# where to reproduce it -- so it is printed in the case identity instead. A key dimension
# with one row per value and identical text in every row is dead weight that reads as
# coverage.

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
    # Neither of these says "with redaction switched on". That is a statement about a
    # CONFIGURATION, and most runs never recorded one -- the documented no-gateway floor
    # has no gateway to configure. The clause is appended only by a run that recorded it.
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

# Why this type, specifically. One clause each, and each says something the others do not:
# a reader who skims only this clause should still learn why the row is worth their morning.
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
    # nosec B105 below and at the phrase table: bandit reads a dict key ending in TOKEN
    # with a string value as a hardcoded credential. These values are English sentences
    # about credentials, never credentials. Suppressed per line rather than per file, so a
    # real secret added here later is still reported.
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


# What a finding with no entity is called. `ci` builds findings WITHOUT specimens by
# design, so it cannot ask WHICH value failed to come back and reports the check once with
# no entity at all. Returning "" there left the published sentence reading "The caller's
# own was not returned." -- in a pull-request comment, as the tool's whole explanation.
_UNNAMED_ENTITY_PHRASE = "protected value"


def entity_phrase(entity_type: str) -> str:
    """What a reader calls this type. ``CREDIT_CARD`` is a corpus label, not English."""
    return (
        _ENTITY_PHRASE.get(entity_type)
        or entity_type.replace("_", " ").lower()
        or _UNNAMED_ENTITY_PHRASE
    )


# Appended only by a run that RECORDED the target's redaction feature as on. Then the
# product was configured to prevent exactly this and did not, which is a stronger finding.
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
    """The canned sentence for one finding. Never empty, never raises on a new entity.

    An unknown entity type degrades to the shared half plus a lowercased label. The corpus
    grows; a `KeyError` on the row that grew it would take out the whole report.
    """
    phrase = entity_phrase(entity_type)
    if not attributed and kind == KIND_LEAK_TO_PROVIDER:
        # No redaction clause either: a run cannot accuse a configuration over traffic it
        # could not attribute to the thing configured.
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
    """One failure, addressed to a reader.

    ``sent`` and ``received`` are SPECIMENS. They reach the operator's terminal and never
    the published artifact; `render(reveal=False)` and `published_dict` do not read them.
    Both default to empty so the published path -- which never has them -- needs no values
    to produce a complete row.
    """

    kind: str
    entity_type: str
    carrier: str
    encoding: str = "plain"
    fragmented: bool = False
    sent: str = ""
    received: str = ""
    seed: str = ""
    # Whether the run RECORDED that the target's redaction feature was on. Only a run that
    # recorded it may say so; see `_REDACTION_WAS_ON_NOTE`.
    redaction_configured: bool = False
    # Whether the request that carried the value was the TARGET's. False means it reached a
    # PUBLIC capture without this run's marker -- reportable, but not the gateway's doing.
    attributed: bool = True


@dataclass
class OperatorSpecimens:
    """This run's own generated values, for the OPERATOR'S TERMINAL only.

    Filled by ``run_http_conformance`` when a caller asks for it, and passed straight to
    the printer. It is deliberately a separate object from the report rather than a field
    on it: a report is written to disk and published, and anything reachable from it will
    eventually be serialized by someone who did not read the rule.
    """

    fixture: dict[str, str] = field(default_factory=dict)
    prompt: str = ""
    client_text: str = ""
    seed: str = ""


# `leak_evidence.channel` says which part of the request carried the value. The reader
# needs a name for the place, not the harness's internal bucket.
_CARRIER_FOR_CHANNEL = {
    "body": "chat-content",
    "headers": "http-header",
    "framing": "chunk-framing",
    "request": "request-line",
    # The per-request pass, which looks at everything the target sent at once. "request"
    # alone reads as a field name; this says where to go looking.
    "all": "upstream-request",
}


def findings_from_report(
    report: dict[str, Any],
    specimens: Optional[OperatorSpecimens] = None,
    *,
    seed: str = "",
    duty: str = "restore",
) -> list[Finding]:
    """Read a v1 HTTP-profile report into findings. Measures nothing; decides nothing.

    Every verdict here was already reached by the scorers: `leak_evidence` names the
    entity, the channel and which matcher fired, and `response_fidelity.passed` says
    whether the caller's values came back. This turns those into rows.

    Without ``specimens`` the rows carry no values, which is exactly what the published
    path needs -- so the published renderer is never in the position of having a specimen
    available to leak.

    ``duty`` is the same switch `selfcheck.verdict_for` and `ci` already take. Under
    ``anonymize`` the gateway is not asked to restore anything, so a value that did not
    come back is the configured behaviour and not a finding. Taking it here rather than
    filtering at each call site means the terminal and the published summary cannot
    disagree about what counts -- which is how this was wrong in both at once.
    """
    if duty not in {"restore", "anonymize"}:
        raise ValueError("duty must be restore or anonymize")
    boundary = report["checks"]["configured_upstream_boundary"]
    fixture = specimens.fixture if specimens else {}
    # The seed is what makes a finding repeatable, and the PUBLISHED path is the one that
    # most needs it -- a reader of the CI summary cannot rerun anything without it. It is
    # not in the report (the fixture block publishes only whether values vary), so a caller
    # that seeded the run passes it here; the specimens carry it for the operator path.
    seed = (specimens.seed if specimens else "") or seed
    # Recorded, never assumed. `selfcheck` and the no-gateway floor record no claim at
    # all, and a run that did not record one is not entitled to say redaction was on.
    configured = bool(
        report.get("redaction_claim", {}).get("configured_for_this_run", False)
    )

    findings: list[Finding] = []
    seen: set[str] = set()
    # The two lists are read SEPARATELY and tagged, not concatenated. Which list an item
    # came from is the only record of whether the request carried this run's marker, and
    # merging them here would throw away the distinction `http_profile` keeps on purpose.
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
                # `normalized` means the value was recovered only after joining what the
                # target sent and stripping separators -- that IS the split case, and it
                # needs the split sentence: no fragment the detector saw was wrong.
                fragmented=item.get("match") == "normalized",
                sent=value,
                received=value,
                seed=seed,
                redaction_configured=configured and attributed,
                attributed=attributed,
            )
        )

    fidelity = report["checks"].get("response_fidelity", {})
    # A one-way anonymizer is SUPPOSED not to restore, and `response_fidelity` is already
    # waived from the required checks for this duty. Reporting it anyway printed "was not
    # restored" under a heading reading "What leaked, and why it matters", on a run whose
    # verdict was CLEAN -- the exact confusion this module exists to prevent, aimed at an
    # operator whose gateway is behaving exactly as configured.
    if duty != "anonymize" and not fidelity.get("passed", True):
        findings.extend(_fidelity_findings(fixture, specimens, seed))
    return findings


def _fidelity_findings(
    fixture: dict[str, str], specimens: Optional[OperatorSpecimens], seed: str
) -> list[Finding]:
    """One row per value that did not come back -- and none for the values that did.

    A run can fail `response_fidelity` on framing alone while every value was restored
    perfectly. Naming those entities sends the operator to audit a vault that is working,
    and costs the report its credibility on the entities that did fail. With no specimens
    the per-entity question cannot be asked, so the check reports itself once, unattributed.
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
    """What the client received where ``value`` stood in the prompt.

    "the user saw: [EMAIL_1]" is the line that tells an operator WHICH fix they need: a
    placeholder means the vault never restored, and something else entirely means the
    response was rewritten. Aligning the two texts is the only way to say it, because the
    harness does not know what placeholder scheme the target uses.

    Empty means nothing stands in its place, which the renderer says in words -- a blank
    right-hand column reads as a formatting bug rather than as a finding.
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
            # Part of the value survived verbatim. Clip to the overlap so the line shows
            # the surviving fragment rather than the whole matching run around it.
            low, high = max(i1, start), min(i2, end)
            pieces.append(response[j1 + (low - i1) : j1 + (high - i1)])
        else:
            pieces.append(response[j1:j2])
    return "".join(pieces).strip()


# --------------------------------------------------------------------------- rendering


def render(finding: Finding, *, reveal: bool) -> list[str]:
    """The block, as lines. ``reveal`` is the whole privacy rule.

    ``reveal=True`` is the operator's terminal: their own values, generated on their own
    machine seconds ago.

    ``reveal=False`` is the published artifact, and it does not read ``sent`` or
    ``received`` at all. That is stronger than stripping them: there is no input a later
    edit could route through this branch, so the published block is a pure function of the
    case identity and stays one.
    """
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
        # For a leak the two shapes are identical, and that identity is still the finding
        # even with the values gone. For a fidelity failure what came back is the TARGET's
        # own text -- a placeholder, or anything at all from a gateway that is misbehaving
        # -- which the harness cannot vouch for and therefore never reproduces.
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


# Not "Seed:" followed by nothing, which reads as a bug. Short, because it appears on
# every block of an unseeded run; the footer says once what it means.
_NO_SEED = "none"


def _safe_seed(seed: str) -> str:
    """The seed is the one part of a block that comes from the operator's command line.

    `ci` renders these blocks inside a fenced code span in a Markdown job summary, so a
    seed carrying a backtick run could close the fence early and let the rest of the
    display be reinterpreted as Markdown in a pull request. A seed is an identifier;
    backticks and line breaks in one carry nothing worth preserving.
    """
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
    """The row as it lands in an artifact. Case identity and canned text, no specimens.

    Built field by field rather than by removing keys from the dataclass: a field added to
    `Finding` later is then absent from the artifact by default, instead of published by
    default and noticed by nobody.
    """
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
