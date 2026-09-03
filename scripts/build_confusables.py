#!/usr/bin/env python3
"""Regenerate ``pii_leak_benchmark/confusables.py`` from the Unicode UTS #39 table.

The benchmark's leak matcher folds non-ASCII look-alikes to ASCII before searching a
capture for a fixture value. The fold table is VENDORED rather than fetched at run
time: ``pii-leak-benchmark`` is stdlib plus ``httpx``, and a harness that reaches the
network to decide what counts as a leak is not one an air-gapped tester can run.

Vendored is not the same as hand-written. This script is the derivation, and the
module it writes records the source version and digest so a reader can re-run it and
diff. Hand-editing the generated module is the failure mode to avoid: a single wrong
row silently changes what the harness can and cannot see.

Usage::

    python scripts/build_confusables.py                 # fetch the current table
    python scripts/build_confusables.py path/to/confusables.txt

Fetching is an explicit, developer-initiated step; nothing in the installed package
does it.
"""

from __future__ import annotations

import hashlib
import sys
import unicodedata
import urllib.request
from pathlib import Path

SOURCE_URL = "https://www.unicode.org/Public/security/latest/confusables.txt"
OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "pii-leak-benchmark"
    / "pii_leak_benchmark"
    / "confusables.py"
)


def load(argument: str | None) -> bytes:
    if argument:
        return Path(argument).read_bytes()
    with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:  # noqa: S310
        return response.read()


def derive(raw: bytes) -> tuple[dict[str, str], int]:
    """Rows the harness needs, and how many single-to-single rows were considered.

    Three filters, in this order. Each one exists to keep the table as small as it can
    be while still being complete for what the matcher does:

    1. Single codepoint on both sides, followed transitively to a fixpoint. A
       multi-character prototype is a spelling equivalence rather than a glyph
       substitution, and folding it would change lengths.
    2. Non-ASCII source, ASCII-alphanumeric target. Never rewrite ASCII -- see the
       generated module's header for why that direction is dangerous.
    3. Drop every row the runtime fold can never consult: sources that NFKD rewrites
       (fullwidth forms, mathematical alphanumerics, ligatures, superscripts) and
       sources carrying a decimal value (every non-Latin digit script). The surviving
       domain is exactly the lookup domain, which is what makes the round trip in
       ``test_inspector_folding.py`` assertable for every row.
    """
    considered = 0
    edges: dict[str, str] = {}
    for line in raw.decode("utf-8-sig").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        fields = [field.strip() for field in line.split(";")]
        if len(fields) < 2:
            continue
        source_points, target_points = fields[0].split(), fields[1].split()
        if len(source_points) != 1 or len(target_points) != 1:
            continue
        considered += 1
        edges[chr(int(source_points[0], 16))] = chr(int(target_points[0], 16))

    table: dict[str, str] = {}
    for source in edges:
        # Follow to a fixpoint rather than taking one step. On 17.0.0 this is
        # MEASURED to add nothing -- the published table is already fully resolved,
        # every target is its own prototype -- and it is kept because the one-step
        # assumption is not stated anywhere in UTS #39 and would fail silently if a
        # future revision introduced an intermediate. Bounded: a confusable set can
        # be cyclic.
        target = source
        for _ in range(16):
            successor = edges.get(target)
            if successor is None or successor == target:
                break
            target = successor
        if source.isascii() or not (target.isascii() and target.isalnum()):
            continue
        folded = target.casefold()
        if len(folded) != 1:
            continue
        # The table's domain is exactly what the runtime fold will ever look up: a
        # character that survives NFKD unchanged, is non-ASCII, and carries no
        # decimal value. Anything else is a dead row, and a dead row is worse than a
        # missing one -- it reads as coverage that is not there.
        #
        # Both exclusions were found by asserting the round trip rather than by
        # reasoning about it. SCRIPT CAPITAL I decomposes to ASCII `I`, so the fold
        # takes its ASCII fast path and the row never fires; BENGALI DIGIT FOUR is
        # listed as confusable with `8`, and shipping that would have made a needle
        # of digits read a four as an eight. `unicodedata.decimal` is the authority
        # on what a digit IS, whatever it resembles.
        if unicodedata.normalize("NFKD", source) != source:
            continue
        if unicodedata.decimal(source, None) is not None:
            continue
        table[source] = folded
    return table, considered


def render(table: dict[str, str], considered: int, digest: str) -> str:
    rows = sorted(table.items(), key=lambda item: (item[1], ord(item[0])))
    ascii_rows = "\n".join(
        f'    "\\U{ord(source):08X}": "{folded}",  # {unicodedata.name(source, "UNNAMED")}'
        for source, folded in rows
    )
    digit_pairs = [
        (source, {"o": "0", "l": "1"}[folded])
        for source, folded in rows
        if folded in ("o", "l")
    ]
    digit_rows = "\n".join(
        f'    "\\U{ord(source):08X}": "{digit}",  # {unicodedata.name(source, "UNNAMED")}'
        for source, digit in digit_pairs
    )
    return f'''"""Vendored Unicode confusables fold: non-ASCII look-alikes to their ASCII prototype.

GENERATED FILE -- do not edit by hand. Run ``scripts/build_confusables.py``.

PROVENANCE. Derived mechanically from the Unicode Security Mechanisms confusables
table (UTS #39), retrieved from

    {SOURCE_URL}

    Version:  17.0.0
    Dated:    2025-07-22 05:49:37 GMT
    sha256:   {digest}

Three filters produce the {len(rows)} rows below, out of {considered} single-codepoint
rows in the source: single codepoint on both sides, FOLLOWED TRANSITIVELY to a
fixpoint; non-ASCII source with an ASCII-alphanumeric target; and drop everything
``unicodedata`` already handles -- NFKD (fullwidth forms, mathematical alphanumerics,
ligatures, superscripts) and ``unicodedata.decimal`` (non-Latin digit scripts), both of
which run BEFORE this table and would make such a row unreachable.

On this revision the transitive step adds nothing: the published table is already
fully resolved. It is kept because UTS #39 does not promise that, and a future
revision that introduced an intermediate prototype would otherwise drop rows silently.

What the source really does NOT give you is worth knowing before reading the table as
complete. Lowercase Greek is largely absent: GREEK SMALL LETTER EPSILON's prototype is
LATIN SMALL LETTER C WITH BAR and stops there, and GREEK SMALL LETTER KAPPA's is LATIN
SMALL LETTER KRA. Unicode does not consider those ASCII-confusable, so neither does
this table. Lowercase Cyrillic, which is the script an attacker actually reaches for,
IS covered -- CYRILLIC SMALL LETTER A, IE, ER and the rest all resolve to ASCII.

WHY ASCII IS NEVER A SOURCE. Rewriting an ASCII character would change how ordinary
English text normalizes, and the leak matcher joins every captured string into one
haystack before searching it. Folding ``o`` to ``0`` across a whole capture
manufactures digit runs nothing ever sent, which is the false-POSITIVE class this
project has already been burned by once -- the round 7 IPv4 finding, where a
normalized address supplied a nine-digit SSN match against a gateway that had redacted
correctly. Non-ASCII sources only means ASCII input normalizes exactly as it did
before this module existed.

THE GAP THIS LEAVES, AND THE SECOND TABLE. UTS #39's prototype for the zero family is
the LETTER ``O`` (``0030 ; 004F``) and for the one family the LETTER ``l``
(``0031 ; 006C``). Folding to the prototype alone therefore turns a Cyrillic
``\\u043e`` into ``o``, and a needle whose digits include ``0`` still will not match.
``CONFUSABLE_TO_DIGIT`` closes that: the same rows, restricted to the ``o`` and ``l``
families, mapped to ``0`` and ``1``. It builds an ADDITIONAL haystack rather than
replacing the first, and it too touches no ASCII character -- so it can only ever
match text that really did contain a non-ASCII look-alike.
"""

from __future__ import annotations

SOURCE_URL = "{SOURCE_URL}"
SOURCE_VERSION = "17.0.0"
SOURCE_SHA256 = "{digest}"

# Non-ASCII look-alike -> its case-folded ASCII prototype.
CONFUSABLE_TO_ASCII: dict[str, str] = {{
{ascii_rows}
}}

# The zero and one families only, mapped to the DIGIT rather than to the letter.
CONFUSABLE_TO_DIGIT: dict[str, str] = {{
{digit_rows}
}}
'''


def main() -> int:
    raw = load(sys.argv[1] if len(sys.argv) > 1 else None)
    digest = hashlib.sha256(raw).hexdigest()
    table, considered = derive(raw)
    OUTPUT.write_text(render(table, considered, digest), encoding="utf-8", newline="\n")
    print(f"{OUTPUT}: {len(table)} rows from {considered} considered, source sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
