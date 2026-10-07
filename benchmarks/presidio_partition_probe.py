"""Partition oracle applied to an EXTERNAL scanner: the Presidio analyzer.

Question under test
-------------------
Is a whole-string PII scanner *chunk-composable*? For a value that whole-string analysis
detects, does scanning the two halves independently -- which is what a chunk-local
streaming integration does -- still protect it?

Scope and fairness
------------------
This is an EXISTENCE CHECK on a bug class. It is **not** a comparison, **not** a
leaderboard row, and **not** a defect report against Presidio. Presidio does not claim to
be a streaming scanner, and applying it per-chunk is the integrator's decision, not
Presidio's. The claim under test is that a whole-string scanner is unsafe to apply
per-chunk -- a property of the integration pattern that any such scanner inherits.

Model of a chunk-local scanner: each chunk is analysed independently, with no retained
state between chunks. That is precisely the pattern the suffix-retention invariant exists
to replace.

Three outcomes are distinguished, because two of them are failures:
  * ``protected``    -- a chunk match covers the whole value; nothing leaks.
  * ``partial``      -- a chunk match covers only part of the value; the remaining
                        characters reach the wire. Worse than a miss: the output looks
                        redacted.
  * ``missed``       -- no chunk matches; the whole value reaches the wire.

Subject: ``mcr.microsoft.com/presidio-analyzer``, stock recognizer registry, no ad-hoc
recognizers, reachable at ``PRESIDIO_ANALYZER_API_BASE`` (default ``127.0.0.1:5002``).

Fixture: the current valid, non-real benchmark fixture. Invalid specimens would repeat the
fixture defect recorded in ``pii-leak-benchmark/README.md``.
"""

from __future__ import annotations

import json
import os
import urllib.request

ANALYZER = os.environ.get("PRESIDIO_ANALYZER_API_BASE", "http://127.0.0.1:5002").rstrip("/") + "/analyze"

FIXTURE = {
    "EMAIL": ("euefmius@example.com", "EMAIL_ADDRESS"),
    "SSN": ("939-38-8264", "US_SSN"),
    "CREDIT_CARD": ("5555-5555-5555-4444", "CREDIT_CARD"),
}

CARRIER_PREFIX = "Please contact the account holder at "
CARRIER_SUFFIX = " for verification before Friday."


def analyze(text: str) -> list[dict]:
    body = json.dumps({"text": text, "language": "en"}).encode()
    req = urllib.request.Request(ANALYZER, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def covered_span(text: str, entity_type: str, start: int, end: int) -> bool:
    """True if some hit of ``entity_type`` covers the whole [start, end) region."""
    return any(
        hit["entity_type"] == entity_type and hit["start"] <= start and hit["end"] >= end
        for hit in analyze(text)
    )


def any_hit(text: str, entity_type: str) -> bool:
    return any(hit["entity_type"] == entity_type for hit in analyze(text))


def classify_split(value: str, entity_type: str, k: int) -> str:
    """Outcome of a chunk-local scanner when the stream splits inside ``value`` at ``k``."""
    left, right = CARRIER_PREFIX + value[:k], value[k:] + CARRIER_SUFFIX
    left_ok = covered_span(left, entity_type, len(CARRIER_PREFIX), len(left))
    right_ok = covered_span(right, entity_type, 0, len(value) - k)
    if left_ok and right_ok:
        return "protected"
    if left_ok or right_ok or any_hit(left, entity_type) or any_hit(right, entity_type):
        return "partial"
    return "missed"


def main() -> None:
    print("Presidio partition probe -- is a whole-string scanner chunk-composable?")
    print(f"analyzer: {ANALYZER}")
    print("=" * 78)
    summary = []

    for label, (value, entity_type) in FIXTURE.items():
        whole = CARRIER_PREFIX + value + CARRIER_SUFFIX
        baseline = covered_span(whole, entity_type, len(CARRIER_PREFIX), len(CARRIER_PREFIX) + len(value))
        print(f"\n{label}  value={value!r}  presidio_type={entity_type}")
        print(f"  whole-string baseline (full span covered): {baseline}")
        if not baseline:
            print("  SKIP -- baseline does not cover the value; probe is meaningless")
            summary.append((label, None))
            continue

        outcomes = {"protected": [], "partial": [], "missed": []}
        for k in range(1, len(value)):
            outcomes[classify_split(value, entity_type, k)].append(k)

        total = len(value) - 1
        print(f"  internal split points: {total}")
        for name in ("protected", "partial", "missed"):
            ks = outcomes[name]
            print(f"    {name:10} {len(ks):3}", end="")
            if ks and name != "protected":
                ex = ks[len(ks) // 2]
                print(f"   e.g. k={ex}: {value[:ex]!r} | {value[ex:]!r}", end="")
            print()
        summary.append((label, (len(outcomes["protected"]), len(outcomes["partial"]), len(outcomes["missed"]), total)))

    print("\n" + "=" * 78)
    print("SUMMARY -- protected / partial / missed of internal split points")
    for label, counts in summary:
        if counts is None:
            print(f"  {label:12} baseline not covered, skipped")
        else:
            protected, partial, missed, total = counts
            print(f"  {label:12} {protected} / {partial} / {missed}   (of {total})")


if __name__ == "__main__":
    main()
