"""Endpoint-neutral HTTP profile for streaming privacy gateway evaluations."""

from __future__ import annotations

import asyncio
import base64
import binascii
import gzip
import json
import os
import platform
import re
import secrets
import statistics
import sys
import threading
import time
import unicodedata
import zlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import unquote_plus, urljoin

import httpx

from pii_leak_benchmark.confusables import CONFUSABLE_TO_ASCII, CONFUSABLE_TO_DIGIT
from pii_leak_benchmark.provenance import build_attestation
from pii_leak_benchmark.redaction_claim import (
    derive_outcome,
    normalize_claim,
    rationale_for,
)

# ---------------------------------------------------------------------------
# The protected fixture. Every value must satisfy BOTH properties at once.
#
# VALID -- a detector that validates its input recognises it. The fixture this
# replaced did not: `123-45-6789` is on Presidio's own invalidation list, the card
# `4532-1234-5678-9012` has Luhn checksum 68, and `.invalid` has no public suffix so
# `tldextract` rejects it. Measured against `mcr.microsoft.com/presidio-analyzer`,
# stock registry, `score_threshold: 0.0`, that fixture produced NO `US_SSN`, NO
# `CREDIT_CARD` and NO `EMAIL_ADDRESS`. The fixture therefore favored shape matching
# over validated detection. The reference implementation's Tier 1 engine uses regex
# matching without Luhn or SSN range rejection, so this block prevents that detector
# design from receiving an unintended fixture advantage. Never reintroduce an invalid
# specimen.
#
# NON-REAL -- the value can never identify a person or route anywhere. Reserved
# space only. This is why the card is DRAWN FROM A PUBLISHED LIST rather than
# generated: a randomly generated Luhn-valid PAN in an issued BIN may be a live
# card, and the harness must never emit one.
# ---------------------------------------------------------------------------

PROTECTED_ENTITY_TYPES = ("EMAIL", "SSN", "CREDIT_CARD")

# RFC 2606 s3 reserves example.com for documentation. It resolves to IANA-operated
# hosts that accept no mail, so the address cannot reach a person -- and `.com` is a
# real public suffix, so `tldextract`-style validation accepts it.
_FIXTURE_EMAIL_DOMAIN = "example.com"
# Letters only and a CONSTANT length. A digit in the local part would join the
# cross-request digit haystacks the SSN and card needles are matched against, which
# is the same reason the capture probe path carries no digits.
_FIXTURE_EMAIL_LOCAL_ALPHABET = "abcdefghijklmnopqrstuvwxyz"
_FIXTURE_EMAIL_LOCAL_LENGTH = 8

# The SSA has never issued a Social Security Number with an area of 900-999, so a
# number from this space cannot belong to anyone. Presidio's `UsSsnRecognizer` does
# not range-check the area, so it still scores these 0.85 -- verified by measurement,
# not assumed.
#
# The group is held to 01-49, which is outside EVERY group range in Presidio's ITIN
# recognizer (50-65, 70-88, 90-92, 94-99). Without that the value is also a
# syntactically valid ITIN and a validating detector may label it `US_ITIN` instead
# of `US_SSN` -- measured: `987-65-4320`, the range the SSA publishes for use in
# ADVERTISING, comes back US_ITIN 0.5 and no US_SSN at all, because Presidio's SSN
# recognizer additionally blacklists the prefix `98765432` outright. The officially
# "safe" SSN is precisely the one a careful detector ignores, which is the whole
# problem in miniature.
_FIXTURE_SSN_AREA = (900, 999)
_FIXTURE_SSN_GROUP = (1, 49)
_FIXTURE_SSN_SERIAL = (1, 9999)
# Presidio's UsSsnRecognizer.invalidate_result rejects a value starting with any of
# these. The generated space cannot produce one, and the generator checks anyway.
_SSN_INVALIDATING_PREFIXES = ("000", "666", "123456789", "98765432", "078051120")

# Published test PANs. Every one is Luhn-valid, 16 digits, and identically grouped as
# dddd-dddd-dddd-dddd, so drawing between them varies the VALUE without varying the
# FORMAT. None is issued to a cardholder. They are enumerated rather than generated
# on purpose: see the NON-REAL note above.
_FIXTURE_TEST_CARDS = (
    "4111111111111111",
    "4012888888881881",
    "4242424242424242",
    "5555555555554444",
    "5105105105105100",
    "6011111111111117",
)

# The shape of each value, published in the report and used by callers that must
# recover the fixture from the prompt they received. A gateway that wants to pass
# now needs a format-based detector rather than three string replacements.
PROTECTED_VALUE_FORMATS = {
    "EMAIL": "[a-z]{8}@example.com",
    "SSN": "ddd-dd-dddd",
    "CREDIT_CARD": "dddd-dddd-dddd-dddd",
}
PROTECTED_VALUE_PATTERNS = {
    "EMAIL": re.compile(r"\b[a-z]{8}@example\.com\b"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "CREDIT_CARD": re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{4}\b"),
}

_PROMPT_TEMPLATE = (
    "Synthetic conformance record {nonce}: contact {email}, SSN {ssn}, card {card}"
)


def _ipv4_can_produce(digits: str) -> bool:
    """Could some dotted-quad IPv4 address normalize to exactly these digits?

    `_normalize` strips separators, so an injected client IP is matched against the
    needle with its dots removed. Round 7 measured exactly this: the single address
    123.45.67.89 normalizes to the old SSN fixture's digits, so a tunnel that adds an
    `x-forwarded-for` header produced an SSN finding against a gateway that had
    redacted correctly. A generated SSN is rejected if any valid IPv4 can collide
    with it, which removes that class of false leak result by construction rather
    than by disclosure.
    """

    def walk(rest: str, parts: int) -> bool:
        if parts == 4:
            return not rest
        if not rest:
            return False
        for size in (1, 2, 3):
            if len(rest) < size:
                break
            octet = rest[:size]
            if size > 1 and octet[0] == "0":
                continue  # 010 and 01 are not how an address is written
            if int(octet) > 255:
                continue
            if walk(rest[size:], parts + 1):
                return True
        return False

    return walk(digits, 0)


def _make_ssn() -> str:
    while True:
        area = _FIXTURE_SSN_AREA[0] + secrets.randbelow(
            _FIXTURE_SSN_AREA[1] - _FIXTURE_SSN_AREA[0] + 1
        )
        group = _FIXTURE_SSN_GROUP[0] + secrets.randbelow(
            _FIXTURE_SSN_GROUP[1] - _FIXTURE_SSN_GROUP[0] + 1
        )
        serial = _FIXTURE_SSN_SERIAL[0] + secrets.randbelow(
            _FIXTURE_SSN_SERIAL[1] - _FIXTURE_SSN_SERIAL[0] + 1
        )
        value = f"{area:03d}-{group:02d}-{serial:04d}"
        digits = value.replace("-", "")
        if any(digits.startswith(prefix) for prefix in _SSN_INVALIDATING_PREFIXES):
            continue  # unreachable from this space, but the invariant is checked
        if all(digit == digits[0] for digit in digits):
            continue
        if _ipv4_can_produce(digits):
            continue
        return value


def make_fixture() -> dict[str, str]:
    """One run's protected values. Same format every run, different values.

    Variation does not prevent format-specific handling on its own -- a target that redacts by FORMAT
    passes either way, which is the point. It raises the cheapest way to pass without
    building anything from three string replacements to a working format detector,
    which is approximately the thing being measured.

    The card is drawn from a fixed published list rather than generated. Generating a
    Luhn-valid PAN would satisfy VALID and break NON-REAL.
    """
    local = "".join(
        _FIXTURE_EMAIL_LOCAL_ALPHABET[
            secrets.randbelow(len(_FIXTURE_EMAIL_LOCAL_ALPHABET))
        ]
        for _ in range(_FIXTURE_EMAIL_LOCAL_LENGTH)
    )
    card = _FIXTURE_TEST_CARDS[secrets.randbelow(len(_FIXTURE_TEST_CARDS))]
    return {
        "EMAIL": f"{local}@{_FIXTURE_EMAIL_DOMAIN}",
        "SSN": _make_ssn(),
        "CREDIT_CARD": "-".join(card[index : index + 4] for index in range(0, 16, 4)),
    }


def fixture_value_space() -> dict[str, int]:
    """How many distinct values each entity can draw from, before rejection.

    Published so a reader can see what value variation is actually worth per entity
    rather than taking "varies per run" on trust. NOMINAL: `_make_ssn` resamples away
    the IPv4-collidable values on top of this, which is not a rounding error -- a
    measured 37.3% of the 9-digit space is some dotted quad with its dots removed, so
    the effective SSN space is about 3.1e7 rather than 4.9e7. It is reported nominal
    because counting the exact residue is a 49-million-iteration walk and does not
    belong on the report path.

    The card number is deliberately the smallest by four orders of magnitude. That is
    not an oversight: it is drawn from a published list because a generated
    Luhn-valid PAN in an issued BIN could be a live card.
    """
    return {
        "EMAIL": len(_FIXTURE_EMAIL_LOCAL_ALPHABET) ** _FIXTURE_EMAIL_LOCAL_LENGTH,
        "SSN": (
            (_FIXTURE_SSN_AREA[1] - _FIXTURE_SSN_AREA[0] + 1)
            * (_FIXTURE_SSN_GROUP[1] - _FIXTURE_SSN_GROUP[0] + 1)
            * (_FIXTURE_SSN_SERIAL[1] - _FIXTURE_SSN_SERIAL[0] + 1)
        ),
        "CREDIT_CARD": len(_FIXTURE_TEST_CARDS),
    }


def extract_fixture(prompt: str) -> dict[str, str]:
    """Recover the fixture from a prompt, by FORMAT.

    Exported because the values now vary per run: anything that needs to act on them
    -- a mock gateway in the test suite, a reference redactor -- must detect them
    rather than compare against a constant.
    """
    found: dict[str, str] = {}
    for entity, pattern in PROTECTED_VALUE_PATTERNS.items():
        match = pattern.search(prompt)
        if match:
            found[entity] = match.group(0)
    return found


# A stable, valid, non-real example of the fixture SHAPE, for documentation and for
# callers that need one value set without running the harness. It is NOT what a run
# uses -- `make_fixture()` is -- so never match a captured request against it.
REFERENCE_FIXTURE = {
    "EMAIL": "jrmccalx@example.com",
    "SSN": "914-27-6083",
    "CREDIT_CARD": "4111-1111-1111-1111",
}
PROMPT = _PROMPT_TEMPLATE.format(
    nonce="bracket-gravel-kettle-pantry-socket",
    email=REFERENCE_FIXTURE["EMAIL"],
    ssn=REFERENCE_FIXTURE["SSN"],
    card=REFERENCE_FIXTURE["CREDIT_CARD"],
)

# Bounds on what the capture server will buffer and inspect from one request.
# Exceeding any of them marks the capture uninspectable, which FAILS the boundary
# check. A budget that silently stopped the walk would let a target hide the PII
# below the limit and read as clean.
_MAX_CAPTURE_BYTES = 8 * 1024 * 1024
_MAX_INSPECT_DEPTH = 64
_MAX_INSPECT_NODES = 200_000
_MAX_DECODE_ROUNDS = 3
# The shortest base64 RUN worth decoding. This was 16, and 16 was a measured false
# pass rather than a judgement call: base64 of an n-byte value is ceil(4n/3)
# characters, so an 11-byte value -- the exact length of a dd-dd-dddd SSN -- encodes
# to a 15-character run and was never even looked at. Measured end to end against the
# `double_base64` evasion gateway, whose outer layer decodes to precisely that
# 15-character inner run: EMAIL and CREDIT_CARD were recovered, SSN came back with a
# needle proximity of 2 of 9. The run still failed, because the email leaked in the
# same request and one leaked entity fails a run -- which is exactly why no test saw
# it. A corpus that puts ONE entity in a case has no second entity to fail on, and
# the same body reads as a clean pass.
#
# 8 is the floor that makes the guarantee statable: any protected value of 6 bytes or
# more is recoverable when it is base64-encoded on its own, with no prefix to pad the
# run out. Below 6 bytes there is no guarantee, and no entity in the corpus registry
# may be shorter than that. Going lower is not free -- every alphanumeric word of that
# length becomes a decode candidate with 8 alignment attempts, three rounds deep --
# and 8 was measured to leave the conforming-gateway tests green, which is where a
# too-eager decoder shows up as a false leak finding.
_BASE64_MIN_RUN = 8
_BASE64_MIN_VALUE_BYTES = 6
_BASE64ISH = re.compile(r"[A-Za-z0-9+/_-]{%d,}={0,2}" % _BASE64_MIN_RUN)
_HEXISH = re.compile(r"(?:[0-9a-fA-F]{2}){8,}")
_NON_ALPHANUM = re.compile(r"[^0-9a-z]+")

# The correlation marker must survive a CORRECT gateway. A random hex nonce is
# high-entropy and a conforming secret detector redacts it. Evocative nouns are worse
# still: `amber`, `olive`, `sage`, `rowan` and `willow` are given names, so a gateway
# with a conforming PERSON detector redacts them and fails a valid implementation.
# These are mundane concrete nouns that no NER model tags as an entity. Five drawn
# from 96 give 96**5 ~= 2**33 combinations, and correlation needs only a majority of
# them back, so redacting one or two still ties the capture to this run. (The prior
# list claimed ~2**32 for four words drawn from 54, which was really 2**23.)
_NONCE_WORDS = (
    "anchor anvil apron awning barrel basin beacon bellows blanket bobbin bolt bracket "
    "bridle bucket buckle cabinet caliper candle canvas cauldron chisel cistern clamp "
    "cleat compass conduit crate crowbar cupboard dowel drawer funnel gasket girder "
    "granite gravel grommet gutter hamper handle hinge hopper jigsaw kettle lantern "
    "latch ladder ledger lever lintel mallet mantle mortar nozzle pallet pantry parcel "
    "pedal pewter piston plank pliers plunger pulley rafter ratchet ridge rivet rudder "
    "rung saddle sandal satchel scaffold shelf shutter sickle skillet socket spatula "
    "spindle spigot stencil stirrup tackle tarpaulin thimble tongs trestle trowel "
    "turbine valve vise winch wedge whisk"
).split()
_NONCE_WORD_COUNT = 5
# A conforming gateway may legitimately redact a minority of the marker words.
_NONCE_MIN_MATCHES = 3


class CaptureUnreachableError(OSError):
    """The capture server did not observe the harness's own probe request.

    Raised BEFORE any target traffic, so no report is produced. Every condition it
    detects -- a hijacked port, a firewall, a broken tunnel, a stray proxy
    environment variable, a dead server -- otherwise produces a schema-valid report
    that fails checks against a gateway which did nothing wrong.

    Deliberately an OSError. It replaces the EADDRINUSE that POSIX used to raise for
    the one hijack shape a bind could refuse, and the CLI already turns an OSError
    from the harness into "Benchmark failed: ..." and exit 2. The replacement must
    fail the same way the thing it replaces did, and it genuinely is a connectivity
    condition.
    """


# The probe path carries a per-run secret so a target cannot address it, and is drawn
# from LETTERS ONLY: a digit in the path would join the cross-request digit haystacks
# that the SSN and card needles are matched against, and the probe
# is that it cannot influence a verdict.
#
# It is appended to the capture BASE url rather than to the origin, and matched as a
# SUFFIX rather than by equality. A tester's reverse proxy may forward only `/v1/*`,
# and one that rewrites the prefix would change the path the capture sees -- either
# would 404 or mismatch an origin-rooted probe and abort a valid run. The 24-letter
# secret is what makes the path unguessable; the prefix carries no security.
_PROBE_PATH_TEMPLATE = "/__conformance_capture_probe__/{token}"
_PROBE_TOKEN_ALPHABET = "abcdefghijklmnopqrstuvwxyz"  # nosec B105 - an alphabet, not a secret
_PROBE_TOKEN_LENGTH = 24
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"})


def _make_probe_token() -> str:
    return "".join(
        _PROBE_TOKEN_ALPHABET[secrets.randbelow(len(_PROBE_TOKEN_ALPHABET))]
        for _ in range(_PROBE_TOKEN_LENGTH)
    )


def _make_nonce() -> str:
    # Draw without replacement. _marker_matches counts distinct words, so a run
    # containing only one or two distinct draws can never meet the threshold even
    # when a conforming gateway forwards the entire marker unchanged.
    remaining = list(_NONCE_WORDS)
    chosen = []
    for _ in range(_NONCE_WORD_COUNT):
        chosen.append(remaining.pop(secrets.randbelow(len(remaining))))
    return "-".join(chosen)


def _build_prompt(nonce: str, fixture: dict[str, str]) -> str:
    return _PROMPT_TEMPLATE.format(
        nonce=nonce,
        email=fixture["EMAIL"],
        ssn=fixture["SSN"],
        card=fixture["CREDIT_CARD"],
    )


# The digit fold is the ASCII fold plus the zero and one families redirected to `0`
# and `1`. Merged once at import: it is applied per character on every captured
# string.
_CONFUSABLE_DIGIT_FOLD = {**CONFUSABLE_TO_ASCII, **CONFUSABLE_TO_DIGIT}


def _fold(text: str, table: dict[str, str]) -> str:
    """Case-fold, decompose, map look-alikes to ASCII, then drop what is left.

    `_NON_ALPHANUM` deletes rather than folds, and deletion is a false PASS: a
    fullwidth SSN `９１４-２７-６０８３` normalized to the empty string, and
    `jrmccalx@examрle.com` with one Cyrillic ER normalized to `jrmccalxexamlecom`,
    so neither needle was ever found. Both are named encodings on the corpus's Axis B
    (`homoglyph`, and the fullwidth case that `numeric-char-array` neighbours), and a
    capture server that cannot decode an encoding it is scoring produces a clean
    report for a gateway that leaked.

    Three layers, cheapest first, and each one exists because the one before it does
    not cover the next:

    1. NFKD -- fullwidth and halfwidth forms, mathematical alphanumerics, ligatures,
       superscripts, and precomposed accents (`josé` now folds to `jose` instead of
       losing its last letter). Combining marks left behind are dropped by the strip.
    2. `unicodedata.decimal` -- every non-Latin digit script. An SSN written in
       Devanagari or Arabic-Indic digits carries the same nine digits and NFKD does
       not touch it, because those are not compatibility equivalents.
    3. The vendored UTS #39 confusables fold -- cross-script glyph look-alikes, which
       neither of the above reaches. See `confusables.py` for the derivation and for
       why no ASCII character is ever a source. It runs AFTER the decimal check on
       purpose: UTS #39 maps DEVANAGARI DIGIT ZERO to the letter `o`, and for a
       needle made of digits the UCD's decimal value is the truth.

    Matching on the result still defeats separator-level obfuscation (unicode escapes,
    inserted punctuation, whitespace, zero-width insertions) and fragments split across
    adjacent string literals, without a decoder per trick.
    """
    folded: list[str] = []
    # Case folding happens AFTER the table lookup, not before. UTS #39 lists both
    # cases as separate rows and their prototypes are not case variants of each
    # other: GREEK CAPITAL LETTER EPSILON resolves to ASCII `E`, while GREEK SMALL
    # LETTER EPSILON resolves to LATIN SMALL LETTER C WITH BAR and never reaches
    # ASCII at all. Case folding first turns every uppercase source into a lowercase
    # one the table may not carry, which silently deletes the row -- measured on the
    # first cut of this function, where a value written in capital Greek normalized
    # to the empty string exactly as it had before the fix.
    for character in unicodedata.normalize("NFKD", text):
        if character.isascii():
            folded.append(character)
            continue
        # Decimal value first, and the order is load-bearing. UTS #39 maps DEVANAGARI
        # DIGIT ZERO to the LETTER `o` -- visually true, semantically wrong for a
        # needle made of digits. A character that carries a decimal value IS that
        # digit, whatever it resembles, so the UCD's own answer wins over the
        # look-alike table.
        decimal = unicodedata.decimal(character, None)
        if decimal is not None:
            folded.append(str(decimal))
            continue
        mapped = table.get(character)
        folded.append(character if mapped is None else mapped)
    return _NON_ALPHANUM.sub("", "".join(folded).casefold())


def _normalize(text: str) -> str:
    """The primary fold: look-alikes become their UTS #39 ASCII prototype."""
    return _fold(text, CONFUSABLE_TO_ASCII)


def _normalize_confusable_digits(text: str) -> str:
    """The secondary fold, for an ADDITIONAL haystack -- never a replacement.

    UTS #39's prototype for the zero family is the letter `O` and for the one family
    the letter `l`, so `_normalize` turns a Cyrillic `о` into `o` and a needle
    whose digits include `0` still does not match. This fold sends those two families
    to `0` and `1` instead.

    It is a second haystack rather than a wider `_normalize` because collapsing the
    letter/digit distinction is only safe while it cannot touch ASCII. Folding ASCII
    `o` to `0` across a joined capture manufactures digit runs nothing ever sent --
    the false-positive class that produced the round 7 IPv4 SSN finding against a
    gateway which had redacted correctly. Restricted to non-ASCII sources, this fold
    can only match text that really did contain a look-alike.
    """
    return _fold(text, _CONFUSABLE_DIGIT_FOLD)


def _decode_candidates(value: str) -> list[str]:
    """Every plausible decoding of a string, so encoded PII is still inspected.

    Scans for encoded RUNS rather than requiring the whole string to be one: a single
    prefix character defeated an anchored whole-string base64 match.
    """
    found: list[str] = []
    for match in _BASE64ISH.findall(value):
        run = match.replace("-", "+").replace("_", "/").rstrip("=")
        # Try every 4-byte alignment. A run captured with a stray leading character
        # decodes to noise at offset 0 while the real payload starts one byte in,
        # which is all it took to hide an encoded value from an anchored match.
        for offset in range(4):
            aligned = run[offset:]
            # Floor of one decodable group, NOT the _BASE64_MIN_RUN characters
            # _BASE64ISH needs to spot a run in the first place. Reusing 16 here meant a 16-character run
            # -- exactly what one prefix character plus an 11-byte value produces --
            # was only ever tried at offset 0, which decodes to noise, so the value
            # was never recovered. The suite did not catch it because the previous
            # fixture's SSN digits (123456789) were a SUBSTRING of its card digits
            # (4532123456789012): every "SSN recovered" assertion was satisfied by
            # decoding the card. Found when the fixture stopped being invalid.
            if len(aligned) < 4:
                break
            # Both truncating to a 4-character boundary and padding up to one:
            # truncating alone drops the final group, which cost the tail of a value
            # and with it the literal match.
            for candidate in (
                aligned[: len(aligned) // 4 * 4],
                aligned + "=" * (-len(aligned) % 4),
            ):
                try:
                    decoded = base64.b64decode(candidate, validate=False).decode("utf-8", "ignore")
                except (binascii.Error, ValueError):
                    continue
                if decoded.strip():
                    found.append(decoded)
    for match in _HEXISH.findall(value):
        try:
            decoded = bytes.fromhex(match[: len(match) // 2 * 2]).decode("utf-8", "ignore")
        except ValueError:
            continue
        if decoded.strip():
            found.append(decoded)
    if "%" in value:
        unquoted = unquote_plus(value)
        if unquoted != value:
            found.append(unquoted)
    return found


class _Inspection:
    """Every string a parsed body carries, plus whether the walk saw all of it.

    DECODED MATERIAL IS KEPT IN ITS OWN LISTS, and that separation is load-bearing
    rather than tidy. `strings` is an ORDERED stream and the cross-fragment matcher
    works by joining it: two halves of an email in adjacent array elements are only
    recoverable because nothing sits between them. Appending a string's decodings
    inline splices that decoded text BETWEEN the two fragments and the join stops
    reassembling. Measured: with the base64 run floor lowered, the eight-character
    prefix of a fragment became a decode candidate, its garbage decoding landed
    between the halves, and `test_fragment_split_inside_a_protected_value_is_
    reassembled` went from finding EMAIL to finding nothing.

    Both are searched. Only the verbatim stream is searched IN ORDER.
    """

    def __init__(self) -> None:
        self.strings: list[str] = []
        self.values: list[str] = []
        self.decoded_strings: list[str] = []
        self.decoded_values: list[str] = []
        self.truncated = False
        self.nodes = 0

    def budget(self) -> bool:
        self.nodes += 1
        if self.nodes > _MAX_INSPECT_NODES:
            self.truncated = True
            return False
        return True


class _JSONObject:
    """JSON object representation that preserves duplicate member occurrences."""

    def __init__(self, pairs: list[tuple[str, Any]]) -> None:
        self.pairs = pairs


def _materialize_json(value: Any) -> Any:
    """Apply normal last-member-wins semantics after inspection is preserved."""
    if isinstance(value, _JSONObject):
        return {key: _materialize_json(item) for key, item in value.pairs}
    if isinstance(value, list):
        return [_materialize_json(item) for item in value]
    return value


def _collect(value: Any, found: _Inspection, depth: int = 0, rounds: int = 0, is_key: bool = False) -> None:
    """Walk a parsed body exhaustively, decoding encoded runs as it goes.

    The leak check runs over this, not over raw wire bytes: any semantics-preserving
    transport encoding (JSON unicode escapes, gzip, base64, hex, char-code arrays,
    fragmented string literals) leaves the wire bytes unrecognizable while the
    upstream still parses the PII.
    """
    if not found.budget():
        return
    if depth > _MAX_INSPECT_DEPTH:
        found.truncated = True
        return
    # `rounds` is already the "did we get here through a decoder" flag, so it also
    # selects which stream this string belongs to. Nothing reached by decoding may
    # enter the ordered verbatim stream -- see `_Inspection`.
    into_strings = found.decoded_strings if rounds else found.strings
    into_values = found.decoded_values if rounds else found.values
    if isinstance(value, str):
        into_strings.append(value)
        if not is_key:
            into_values.append(value)
        if rounds < _MAX_DECODE_ROUNDS:
            for decoded in _decode_candidates(value):
                _collect(decoded, found, depth, rounds + 1)
        return
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        # Numbers carry PII too: a digit string survives as an integer, and a
        # character-code array reconstructs upstream while holding no strings at all.
        text = repr(value)
        into_strings.append(text)
        if not is_key:
            into_values.append(text)
        return
    if isinstance(value, _JSONObject):
        # json.loads normally collapses duplicate members before the object walk.
        # Preserve and inspect every occurrence: earlier values reached the capture
        # origin and first-wins JSON parsers can expose them to a real upstream.
        for key, item in value.pairs:
            _collect(str(key), found, depth + 1, rounds, is_key=True)
            _collect(item, found, depth + 1, rounds)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _collect(str(key), found, depth + 1, rounds, is_key=True)
            _collect(item, found, depth + 1, rounds)
        return
    if isinstance(value, (list, tuple)):
        codes = [item for item in value if isinstance(item, int) and not isinstance(item, bool)]
        if codes and len(codes) == len(value) and all(0 < code < 0x110000 for code in codes):
            # A reconstruction, not something the target literally wrote, so it goes
            # to the decoded stream for the same reason a base64 decoding does.
            decoded = "".join(chr(code) for code in codes)
            found.decoded_strings.append(decoded)
            found.decoded_values.append(decoded)
        for item in value:
            _collect(item, found, depth + 1, rounds)
        return
    found.truncated = True


def _collect_framing_metadata(kind: str, text: str, found: _Inspection) -> None:
    """Collect framing syntax while separating structural names from data values."""
    # Keep the complete syntax in strings so a value carried in an unusual but
    # parseable shape is still visible within one request. Structural names do not
    # belong in the values-only cross-request join.
    _collect(text, found, is_key=True)
    if kind == "trailer":
        name, separator, value = text.partition(":")
        if separator:
            _collect(name.strip(), found, is_key=True)
            _collect(value.strip(), found)
        return

    # Chunk extensions are `;`-separated name[=value] parameters. This bounded
    # lexer respects quoted semicolons and escapes sufficiently to recover each
    # parameter value without discarding the raw extension above.
    position = 0
    length = len(text)
    while position < length:
        while position < length and (text[position].isspace() or text[position] == ";"):
            position += 1
        name_start = position
        while position < length and text[position] not in "=;":
            position += 1
        name = text[name_start:position].strip()
        if name:
            _collect(name, found, is_key=True)
        if position >= length or text[position] != "=":
            continue
        position += 1
        while position < length and text[position].isspace():
            position += 1
        if position < length and text[position] == '"':
            position += 1
            characters: list[str] = []
            while position < length:
                character = text[position]
                position += 1
                if character == '"':
                    break
                if character == "\\" and position < length:
                    character = text[position]
                    position += 1
                characters.append(character)
            _collect("".join(characters), found)
        else:
            value_start = position
            while position < length and text[position] != ";":
                position += 1
            _collect(text[value_start:position].strip(), found)


def _needle_proximity(needle: str, haystacks: tuple[str, ...]) -> int:
    """Longest contiguous run OF THE NEEDLE found anywhere in these haystacks.

    This is the collision margin. Equal to len(needle) means the value is present --
    a leak. Anything less is how close the run came: round 6 measured 2 of 9 for the
    SSN on a validated loopback run, a 7-character margin, and concluded no benign
    reassembly was constructible.

    Deliberately NOT "longest digit run in the haystack": measured against the live
    proxy that number is 16, and it is the gateway's own synthetic REPLACEMENT card
    standing in for the fixture. A correctly-redacting gateway emits substitutes shaped
    like the fixture, so a raw digit-run count is dominated by them and tells a reader
    nothing about headroom.
    """
    longest = 0
    length = len(needle)
    for start in range(length):
        # Only runs longer than the best so far can improve it.
        for end in range(length, start + longest, -1):
            fragment = needle[start:end]
            if any(fragment in hay for hay in haystacks):
                longest = max(longest, end - start)
                break
    return longest


def _leaked_entities(
    records: list[dict[str, Any]], fixture: dict[str, str]
) -> tuple[list[str], list[dict[str, Any]], int]:
    """Entity types whose fixture value reached the upstream in any recoverable form.

    Returns the sorted entity list (unchanged in meaning), the evidence behind each
    finding, and the longest contiguous digit run seen in any normalized haystack.
    """
    leaked: set[str] = set()
    evidence: list[dict[str, Any]] = []
    proximity: dict[str, int] = {entity: 0 for entity in fixture}

    def inspect(
        strings: list[str],
        values: list[str],
        decoded_strings: list[str],
        decoded_values: list[str],
        channel: str,
        scope: str,
    ) -> None:
        # Two joins, and the difference between them is the whole reason decoded
        # material is kept apart. The first preserves the ORDER the target wrote in,
        # so two halves of a value in adjacent fields still sit next to each other.
        # The second appends everything a decoder recovered, which is where an
        # encoded value is found. Searching only the second would break fragment
        # reassembly; searching only the first would miss every encoding.
        joined = (
            "".join(strings),
            "".join(values),
            "".join(strings + decoded_strings),
            "".join(values + decoded_values),
        )
        folded = tuple(_normalize(text) for text in joined)
        # The digit fold is a separate pair, and a needle is only ever matched against
        # haystacks folded the same way it was. Comparing a digit-folded needle
        # against a letter-folded capture would report a match that neither form
        # contains. Fixture values are ASCII today, so the two needles are identical
        # and this costs nothing; a corpus entity in a non-Latin script makes them
        # differ, and then the pairing is what keeps the finding true.
        digit_folded = tuple(_normalize_confusable_digits(text) for text in joined)
        for entity, value in fixture.items():
            proximity[entity] = max(
                proximity[entity],
                _needle_proximity(_normalize(value), joined + folded),
                _needle_proximity(_normalize_confusable_digits(value), digit_folded),
            )
        for entity, value in fixture.items():
            if entity in leaked:
                continue
            if any(value in text for text in strings) or any(
                value in text for text in decoded_strings
            ):
                # The fixture value appears verbatim in something the target sent.
                # This is the strong form and needs no interpretation.
                leaked.add(entity)
                evidence.append(
                    {
                        "entity_type": entity,
                        "channel": channel,
                        "scope": scope,
                        "match": "literal",
                    }
                )
                continue
            normalized = _normalize(value)
            digit_normalized = _normalize_confusable_digits(value)
            if (
                any(value in hay or normalized in hay for hay in joined + folded)
                or any(digit_normalized in hay for hay in digit_folded)
            ):
                # Recovered only after joining and stripping separators, or after
                # folding a non-ASCII look-alike back to ASCII. Still a finding --
                # this is what catches deliberate obfuscation -- but a purely numeric
                # needle can in principle collide with unrelated digits, so the
                # artifact says which matcher fired. Both folds report as
                # "normalized": the v1.0.0 schema's match enum is frozen, and a
                # confusable hit IS a normalized-form hit, so this understates how
                # the value was found rather than misstating it.
                leaked.add(entity)
                evidence.append(
                    {
                        "entity_type": entity,
                        "channel": channel,
                        "scope": scope,
                        "match": "normalized",
                    }
                )

    for record in records:
        inspect(
            record["strings"],
            record["values"],
            record.get("decoded_strings", []),
            record.get("decoded_values", []),
            "all",
            "per-request",
        )

    # A stateful upstream can reassemble one logical value from ordered requests.
    # Join each data channel independently across records so ordinary method/header
    # metadata does not break a body-to-body (or trailer-to-trailer) reconstruction.
    for channel in ("request", "headers", "framing", "body"):

        def gather(suffix: str, channel: str = channel) -> list[str]:
            return [
                item
                for record in records
                for item in record.get(f"{channel}_{suffix}", [])
            ]

        inspect(
            gather("strings"),
            gather("values"),
            gather("decoded_strings"),
            gather("decoded_values"),
            channel,
            "cross-request",
        )
    return sorted(leaked), evidence, proximity


def _marker_matches(record: dict[str, Any], words: list[str]) -> int:
    """How many marker words survived into this captured request."""
    hay = _normalize("".join(record["strings"]))
    return sum(1 for word in dict.fromkeys(words) if _normalize(word) in hay)


def _decode_content_encoding(body: bytes, content_encoding: str) -> tuple[bytes, Optional[str]]:
    encoding = (content_encoding or "").strip().lower()
    if not encoding or encoding == "identity":
        return body, None
    try:
        if encoding == "gzip":
            return gzip.decompress(body), None
        if encoding in ("deflate", "zlib"):
            return zlib.decompress(body), None
    except (OSError, EOFError, zlib.error):
        return body, f"undecodable_{encoding}"
    return body, f"unsupported_encoding_{encoding}"


def _timestamp() -> str:
    source_epoch = os.getenv("SOURCE_DATE_EPOCH")
    current = (
        datetime.fromtimestamp(int(source_epoch), tz=timezone.utc)
        if source_epoch is not None
        else datetime.now(timezone.utc)
    )
    return current.isoformat().replace("+00:00", "Z")


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    if not ordered:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0}

    def at(fraction: float) -> float:
        return ordered[min(int((len(ordered) - 1) * fraction), len(ordered) - 1)]

    return {
        "mean": statistics.fmean(ordered),
        "p50": at(0.50),
        "p95": at(0.95),
        "p99": at(0.99),
    }


class _CaptureState:
    """Records what the upstream actually received, decoded, not as raw wire bytes."""

    def __init__(self, probe_path: str, capture_token: Optional[str] = None) -> None:
        self.lock = threading.Lock()
        self.records: list[dict[str, Any]] = []
        self.probe_path = probe_path.rstrip("/")
        self.capture_token = capture_token

    def classify(self, path: Any, headers: Any) -> tuple[bool, bool]:
        """Is this the harness's own probe, and did it present the capture token?

        Neither answer may ever cause a request to go unrecorded. A capture bound to
        a public interface WILL receive unrelated traffic -- scanners,
        someone else's misconfiguration -- and dropping it would hide exactly the
        condition a reader needs to weigh the run. Both flags only decide which
        bucket a record is reported in.
        """
        probe = str(path or "").split("?")[0].rstrip("/").endswith(self.probe_path)
        if self.capture_token is None:
            return probe, True
        presented: list[str] = []
        if headers is not None:
            for value in headers.get_all("authorization") or ():
                text = str(value).strip()
                if text.lower().startswith("bearer "):
                    presented.append(text[7:].strip())
            for value in headers.get_all("x-conformance-capture-token") or ():
                presented.append(str(value).strip())
        authenticated = any(
            secrets.compare_digest(item, self.capture_token) for item in presented
        )
        return probe, authenticated

    def append(self, record: dict[str, Any]) -> None:
        with self.lock:
            self.records.append(record)

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.records)


def _handler_for(state: _CaptureState):
    class CaptureHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def handle_one_request(self) -> None:
            self._capture_recorded = False
            super().handle_one_request()

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def send_error(
            self,
            code: int,
            message: Optional[str] = None,
            explain: Optional[str] = None,
        ) -> None:
            # parse_request can reject an unsupported HTTP version or malformed
            # request before verb dispatch. Record that connection as uninspectable;
            # otherwise a decoy valid POST can hide arbitrary unparsed transport data.
            if not getattr(self, "_capture_recorded", False):
                found = _Inspection()
                raw_request_line = getattr(self, "raw_requestline", b"")
                if isinstance(raw_request_line, bytes):
                    _collect(raw_request_line.decode("latin-1", "replace"), found)
                else:
                    _collect(str(raw_request_line), found)
                headers = getattr(self, "headers", None)
                if headers is not None:
                    for header_name, header_value in headers.items():
                        _collect(str(header_name), found)
                        _collect(unquote_plus(str(header_value)), found)
                probe, authenticated = state.classify(
                    getattr(self, "path", ""), getattr(self, "headers", None)
                )
                state.append(
                    {
                        "path": getattr(self, "path", ""),
                        "method": getattr(self, "command", ""),
                        "probe": probe,
                        "authenticated": authenticated,
                        "parsed": False,
                        "strings": found.strings,
                        "values": found.values,
                        "decoded_strings": found.decoded_strings,
                        "decoded_values": found.decoded_values,
                        "request_strings": list(found.strings),
                        "request_values": list(found.values),
                        "request_decoded_strings": list(found.decoded_strings),
                        "request_decoded_values": list(found.decoded_values),
                        "headers_strings": [],
                        "headers_values": [],
                        "framing_strings": [],
                        "framing_values": [],
                        "body_strings": [],
                        "body_values": [],
                        "byte_length": 0,
                        "error": f"http_protocol_error_{code}",
                    }
                )
                self._capture_recorded = True
            super().send_error(code, message, explain)

        def do_GET(self) -> None:  # noqa: N802
            record, _ = self._capture()
            if record["probe"]:
                # Echo the secret back. The probe must prove that the socket the
                # harness reached is THIS server, not another process that bound the
                # same address -- a 404 or any other body from a squatter fails it.
                body = json.dumps(
                    {"conformance_capture_probe": state.probe_path}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.rstrip("/") == "/v1/models":
                body = json.dumps(
                    {"object": "list", "data": [{"id": "conformance-model", "object": "model"}]}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)

        def _read_body(self) -> tuple[bytes, Optional[str], list[tuple[str, str]]]:
            """Read the body under either framing.

            BaseHTTPRequestHandler does not decode Transfer-Encoding: chunked, and
            httpx/aiohttp/requests all switch to chunked automatically for streaming
            bodies. Reading content-length alone yields an empty capture that would
            otherwise be indistinguishable from a clean one.
            """
            transfer_encoding = (self.headers.get("transfer-encoding") or "").lower()
            framing_metadata: list[tuple[str, str]] = []
            if "chunked" in transfer_encoding:
                chunks: list[bytes] = []
                total = 0
                metadata_bytes = 0
                while True:
                    raw_size_line = self.rfile.readline(65536)
                    if not raw_size_line:
                        return b"".join(chunks), "truncated_chunked_body", framing_metadata
                    if len(raw_size_line) == 65536 and not raw_size_line.endswith(b"\n"):
                        return b"".join(chunks), "chunk_metadata_too_large", framing_metadata
                    metadata_bytes += len(raw_size_line)
                    if metadata_bytes > _MAX_CAPTURE_BYTES:
                        return b"".join(chunks), "chunk_metadata_too_large", framing_metadata
                    size_line = raw_size_line.strip()
                    size_token, separator, extension = size_line.partition(b";")
                    if separator:
                        framing_metadata.append(
                            ("chunk_extension", unquote_plus(extension.decode("latin-1")))
                        )
                    try:
                        size = int(size_token, 16)
                    except ValueError:
                        return b"".join(chunks), "malformed_chunk_header", framing_metadata
                    if size == 0:
                        while True:
                            trailer = self.rfile.readline(65536)
                            if trailer in (b"\r\n", b"\n", b""):
                                break
                            if len(trailer) == 65536 and not trailer.endswith(b"\n"):
                                return b"".join(chunks), "trailer_too_large", framing_metadata
                            metadata_bytes += len(trailer)
                            if metadata_bytes > _MAX_CAPTURE_BYTES:
                                return b"".join(chunks), "trailer_too_large", framing_metadata
                            framing_metadata.append(
                                ("trailer", unquote_plus(trailer.decode("latin-1").strip()))
                            )
                        return b"".join(chunks), None, framing_metadata
                    total += size
                    if total > _MAX_CAPTURE_BYTES:
                        return b"".join(chunks), "body_too_large", framing_metadata
                    chunk = self.rfile.read(size)
                    if len(chunk) != size:
                        return b"".join(chunks), "truncated_chunked_body", framing_metadata
                    chunks.append(chunk)
                    if self.rfile.read(2) != b"\r\n":
                        return b"".join(chunks), "malformed_chunk_terminator", framing_metadata
            raw_length = self.headers.get("content-length")
            if raw_length is None:
                # An HTTP request with neither Transfer-Encoding nor Content-Length
                # has a zero-length body; it is not close-delimited like a response.
                return b"", None, framing_metadata
            try:
                length = int(raw_length)
            except ValueError:
                return b"", "invalid_content_length", framing_metadata
            if length < 0:
                return b"", "invalid_content_length", framing_metadata
            if length > _MAX_CAPTURE_BYTES:
                return b"", "body_too_large", framing_metadata
            body = self.rfile.read(length)
            if len(body) != length:
                return body, "truncated_body", framing_metadata
            return body, None, framing_metadata

        def _capture(self) -> tuple[dict[str, Any], Any]:
            body, framing_error, framing_metadata = self._read_body()
            body, encoding_error = _decode_content_encoding(
                body, self.headers.get("content-encoding", "")
            )
            probe, authenticated = state.classify(self.path, self.headers)
            record: dict[str, Any] = {
                "path": self.path,
                "method": self.command,
                "probe": probe,
                "authenticated": authenticated,
                "parsed": False,
                "strings": [],
                "values": [],
                "decoded_strings": [],
                "decoded_values": [],
                "byte_length": len(body),
                "error": framing_error or encoding_error,
            }
            payload = None
            found = _Inspection()

            def mark() -> tuple[int, int, int, int]:
                return (
                    len(found.strings),
                    len(found.values),
                    len(found.decoded_strings),
                    len(found.decoded_values),
                )

            def slice_since(marker: tuple[int, int, int, int], channel: str) -> None:
                """Record one channel's own contribution, verbatim and decoded apart.

                Four cursors rather than two: decoded material lives in its own
                stream so it cannot land between two ordered fragments, and a
                cross-request join has to be able to reassemble a channel from the
                verbatim stream alone.
                """
                record[f"{channel}_strings"] = found.strings[marker[0]:]
                record[f"{channel}_values"] = found.values[marker[1]:]
                record[f"{channel}_decoded_strings"] = found.decoded_strings[marker[2]:]
                record[f"{channel}_decoded_values"] = found.decoded_values[marker[3]:]

            request_mark = mark()
            # Every component of the request line is inspected. A query string is as
            # much an egress channel as a body, and custom methods are attacker-chosen.
            _collect(self.command, found)
            _collect(unquote_plus(self.path), found)
            slice_since(request_mark, "request")
            # So are request headers. A gateway can redact the visible message field
            # and carry the raw values in metadata headers instead; the upstream
            # receives them either way, so an unwalked header is an unwatched channel.
            header_mark = mark()
            for header_name, header_value in self.headers.items():
                _collect(str(header_name), found, is_key=True)
                _collect(unquote_plus(str(header_value)), found)
            slice_since(header_mark, "headers")
            header_defects = getattr(self.headers, "defects", ())
            if header_defects:
                # email.message intentionally recovers from malformed header lines
                # and can discard them. Recovery is useful for a server, but a
                # measurement oracle cannot certify bytes its parser did not expose.
                defect_names = sorted({type(defect).__name__ for defect in header_defects})
                record["error"] = record["error"] or (
                    "malformed_headers:" + ",".join(defect_names)
                )
            framing_mark = mark()
            for kind, item in framing_metadata:
                _collect_framing_metadata(kind, item, found)
            slice_since(framing_mark, "framing")
            record["body_strings"] = []
            record["body_values"] = []
            record["body_decoded_strings"] = []
            record["body_decoded_values"] = []
            try:
                inspection_payload = json.loads(body, object_pairs_hook=_JSONObject)
                body_mark = mark()
                _collect(inspection_payload, found)
                slice_since(body_mark, "body")
                if found.truncated:
                    # Fail closed. A body too deep or too large to walk has NOT been
                    # shown to be clean, and a target can choose how deep to nest.
                    record["error"] = record["error"] or "inspection_truncated"
                else:
                    # The echo endpoint needs normal last-member-wins semantics.
                    # Materialize only after the bounded walk, so over-deep input
                    # cannot recurse here before its fail-closed record is appended.
                    payload = _materialize_json(inspection_payload)
                # A declared content-encoding the capture could not apply means the
                # bytes walked are not the bytes a real upstream would decode, even
                # when they happen to parse as JSON on their own.
                record["parsed"] = not record["error"]
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError) as exc:
                if body.strip():
                    record["error"] = record["error"] or f"unparsable_body:{type(exc).__name__}"
                else:
                    # A bodyless request (a GET, say) is fully inspected via its
                    # request line; absence of JSON is not an inspection failure.
                    record["parsed"] = not record["error"] and not found.truncated
            record["strings"] = found.strings
            record["values"] = found.values
            record["decoded_strings"] = found.decoded_strings
            record["decoded_values"] = found.decoded_values
            # Always recorded, parsed or not: an inspection failure must be able to
            # fail the boundary check rather than silently count as a clean capture.
            state.append(record)
            self._capture_recorded = True
            return record, payload

        def do_POST(self) -> None:  # noqa: N802
            # Every path is captured and inspected, not just chat/completions. The
            # boundary under test is the configured upstream ORIGIN: a target that
            # posts raw PII to a sibling endpoint (embeddings, moderations, a
            # telemetry route) has leaked it just as surely.
            record, payload = self._capture()
            if self.path.rstrip("/") != "/v1/chat/completions":
                self.send_error(404)
                return
            if not record["parsed"]:
                self.send_error(400)
                return
            try:
                messages = payload.get("messages", [])
                content = messages[-1].get("content", "") if messages else ""
                if not isinstance(content, str):
                    content = json.dumps(content, separators=(",", ":"))
            except (AttributeError, IndexError, TypeError):
                self.send_error(400)
                return

            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-cache")
            self.send_header("connection", "close")
            self.end_headers()
            for character in content:
                event = {"choices": [{"delta": {"content": character}}]}
                line = f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        # Any other verb is still egress. Capture it, then refuse it.
        def do_PUT(self) -> None:  # noqa: N802
            self._capture()
            self.send_error(405)

        do_PATCH = do_PUT
        do_DELETE = do_PUT
        do_OPTIONS = do_PUT

        def __getattr__(self, name: str):
            # BaseHTTPRequestHandler otherwise emits 501 before _capture for HEAD,
            # TRACE, CONNECT, and arbitrary extension methods. The bytes reached the
            # upstream boundary and must be inspected even when the route is refused.
            if name.startswith("do_"):
                return self.do_PUT
            raise AttributeError(name)

    return CaptureHandler


def _probe_once(
    url: str,
    probe_path: str,
    capture_token: Optional[str],
    timeout_seconds: float,
) -> tuple[bool, str, float]:
    """One probe request. Returns (answered_by_our_capture, detail, elapsed_ms)."""
    headers = {"user-agent": "pii-leak-benchmark-probe"}
    if capture_token:
        headers["authorization"] = f"Bearer {capture_token}"
    started = time.perf_counter()
    try:
        # trust_env=False on purpose: an HTTP_PROXY/ALL_PROXY variable in the tester's
        # environment would route the probe away from the capture and report a
        # reachability the target does not have.
        with httpx.Client(timeout=timeout_seconds, trust_env=False) as client:
            response = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return False, f"unreachable:{type(exc).__name__}", (time.perf_counter() - started) * 1_000
    elapsed_ms = (time.perf_counter() - started) * 1_000
    if response.status_code != 200:
        return False, f"wrong_responder:http_{response.status_code}", elapsed_ms
    try:
        echoed = response.json().get("conformance_capture_probe")
    except (json.JSONDecodeError, AttributeError, ValueError):
        echoed = None
    if echoed != probe_path:
        return False, "wrong_responder:echo_mismatch", elapsed_ms
    return True, "ok", elapsed_ms


def _self_probe(
    local_url: str,
    advertised_url: Optional[str],
    probe_path: str,
    state: _CaptureState,
    capture_token: Optional[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    """Send the harness's own request to the capture and confirm it was recorded.

    This replaces trying to make the bind refuse a stolen port. Measured on Windows
    11, clearing allow_reuse_address did not prevent the hijack: the steal happens
    when the two sockets bind DIFFERENT addresses, and there it happens with or
    without the flag, while the matching-address case was already refused before the
    change. Confirmed end to end -- with that fix applied, a pre-existing loopback
    listener took every request and the capture recorded zero.

    Probing the channel instead of tuning the flag fails closed on every hijack shape
    on every platform, and also catches a firewall, a stray proxy environment
    variable, and a dead server.

    Two probes, deliberately asymmetric:

    local_url is the bind address, always reachable from the harness itself. Any
    failure here aborts: the capture is hijacked, dead, or firewalled, and a run would
    measure nothing while emitting a schema-valid report that blames the target.

    advertised_url is what the TARGET is configured with. When it answers with
    something other than this capture, that is positive evidence of a hijacked public
    address and also aborts. When it is merely unreachable, the result is RECORDED and
    the run continues: a legitimate configuration can be reachable from the target and
    not from the harness -- host.docker.internal resolves inside a container and often
    not on the host. Aborting there would reject a valid setup, so the limit is
    published instead. A broken tunnel still fails the boundary check on zero
    captures, and advertised_url_reachable false is what tells the reader why.
    """
    answered, detail, elapsed_ms = _probe_once(
        local_url, probe_path, capture_token, timeout_seconds
    )
    if not answered:
        raise CaptureUnreachableError(
            f"capture self-probe to {local_url} failed ({detail}). The capture server "
            "is not reachable and recording at its own bind address, so this run would "
            "measure nothing. Another process may hold the port."
        )
    recorded = [record for record in state.snapshot() if record.get("probe")]
    if not recorded:
        raise CaptureUnreachableError(
            f"capture self-probe to {local_url} was answered but not recorded. The "
            "capture server is not recording the traffic it serves."
        )
    if capture_token and not any(record.get("authenticated") for record in recorded):
        raise CaptureUnreachableError(
            f"capture self-probe to {local_url} was recorded but its capture token was "
            "not seen, so traffic the target sends with that token could not be "
            "attributed to it."
        )
    probe: dict[str, Any] = {
        "performed": True,
        "url": local_url,
        "recorded": True,
        "round_trip_ms": elapsed_ms,
    }
    if advertised_url and advertised_url != local_url:
        reachable, advertised_detail, _ = _probe_once(
            advertised_url, probe_path, capture_token, timeout_seconds
        )
        if not reachable and advertised_detail.startswith("wrong_responder"):
            raise CaptureUnreachableError(
                f"capture self-probe to the advertised URL {advertised_url} was "
                f"answered by something other than this capture ({advertised_detail}). "
                "The address the target will be configured with does not reach this run."
            )
        probe["advertised_url"] = advertised_url
        probe["advertised_url_reachable"] = reachable
        probe["advertised_url_detail"] = advertised_detail
    return probe


async def _exercise_target(
    target_base_url: str,
    api_key: str,
    model: str,
    iterations: int,
    timeout_seconds: float,
    extra_headers: dict[str, str],
    prompt: str,
    session_namespace: str,
) -> dict[str, Any]:
    url = urljoin(target_base_url.rstrip("/") + "/", "chat/completions")
    headers = {"authorization": f"Bearer {api_key}", **extra_headers}
    durations_ms: list[float] = []
    # Scored per iteration. Keeping only the last one lets a target that mangles
    # every earlier iteration report clean on the strength of its final response.
    texts: list[str] = []
    event_counts: list[int] = []
    invalid_counts: list[int] = []
    marker_counts: list[int] = []
    content_types: list[str] = []
    status_codes: list[int] = []
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for iteration in range(iterations):
            started = time.perf_counter()
            reconstructed: list[str] = []
            event_count = 0
            invalid_events = 0
            done_markers = 0
            try:
                async with client.stream(
                    "POST",
                    url,
                    # Namespace sessions by run. Reusing conformance-0/1/2 across
                    # invocations makes a compliant loop circuit breaker reject a
                    # later profile even though every request was redacted correctly.
                    headers={
                        **headers,
                        "x-session-id": f"conformance-{session_namespace}-{iteration}",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": True,
                    },
                ) as response:
                    status_codes.append(response.status_code)
                    content_types.append(response.headers.get("content-type", ""))
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            done_markers += 1
                            continue
                        try:
                            event = json.loads(data)
                            value = event.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if isinstance(value, str):
                                reconstructed.append(value)
                            event_count += 1
                        except (AttributeError, IndexError, json.JSONDecodeError, TypeError):
                            invalid_events += 1
                durations_ms.append((time.perf_counter() - started) * 1_000)
                texts.append("".join(reconstructed))
                event_counts.append(event_count)
                invalid_counts.append(invalid_events)
                marker_counts.append(done_markers)
            except (httpx.HTTPError, UnicodeError) as exc:
                errors.append(type(exc).__name__)

    completed = len(texts)
    return {
        "durations_ms": durations_ms,
        "iterations_requested": iterations,
        "iterations_completed": completed,
        "iterations_matching": sum(1 for text in texts if text == prompt),
        "text_matches": completed == iterations and all(text == prompt for text in texts),
        "events": min(event_counts) if event_counts else 0,
        "events_max": max(event_counts) if event_counts else 0,
        "invalid_events": sum(invalid_counts),
        "done_markers_valid": completed == iterations and all(m == 1 for m in marker_counts),
        "content_type_valid": completed == iterations
        and all("text/event-stream" in ct.lower() for ct in content_types),
        "content_type": content_types[-1] if content_types else "",
        "status_codes": sorted(set(status_codes)),
        "errors": errors,
    }


def run_http_conformance(
    target_base_url: str,
    *,
    api_key: str = "conformance-key",
    model: str = "conformance-model",
    implementation_name: str = "external-openai-compatible-endpoint",
    implementation_version: str = "unspecified",
    iterations: int = 3,
    timeout_seconds: float = 30.0,
    capture_host: str = "127.0.0.1",
    capture_port: int = 8765,
    capture_token: Optional[str] = None,
    capture_public_url: Optional[str] = None,
    extra_headers: Optional[dict[str, str]] = None,
    redaction_claim: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Evaluate an OpenAI-compatible endpoint against a controlled capture upstream.

    Configure the target gateway's upstream base URL to the capture server before running.
    Use ``capture://self`` as the target to record an explicit raw-pass-through baseline.

    Two capture modes:

    ``loopback`` (default) -- the capture binds ``127.0.0.1`` and only a target on the
    tester's own machine or network can reach it. This is the stronger observation:
    every request that arrives is the target's.

    ``public`` -- the tester binds a reachable interface (``capture_host="0.0.0.0"``)
    and/or fronts the capture with their own tunnel or VPS, then passes the
    externally reachable base URL as ``capture_public_url`` so the harness advertises,
    probes and correlates against the address the target will actually use. This is
    what makes a hosted gateway measurable at all, and it is a WEAKER observation: the
    address is reachable by anyone. ``capture_token`` is therefore REQUIRED in this
    mode; the target is configured with it as its upstream API key, and requests that
    do not present it are recorded and reported as unattributed rather than counted
    as the target's. The project does not operate a capture service, so the tester
    deploys and controls the capture endpoint.

    TLS is assumed to be terminated by the tester's tunnel; the capture itself speaks
    plaintext HTTP/1.x behind it.

    ``redaction_claim`` records what the product CLAIMS about PII redaction, with a
    citation, and what was configured for this run. It governs the derived ``outcome``,
    which is what a published table cell is allowed to say. Omitting it yields
    ``outcome: claim-unstated`` -- valid measurements, not publishable as a row. See
    ``conformance/redaction_claim.py``: a product that never offered redaction must
    never be printed as "Fail".
    """
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    # Validated before the capture server binds: a malformed claim should cost nothing
    # and must never be silently defaulted into a verdict.
    claim_block = normalize_claim(redaction_claim)

    bind_is_loopback = capture_host in _LOOPBACK_HOSTS
    capture_mode = "loopback" if bind_is_loopback and not capture_public_url else "public"
    if not bind_is_loopback and not capture_public_url:
        raise ValueError(
            f"capture_host={capture_host!r} is not loopback, so capture_public_url is "
            "required (CLI: --capture-public-url URL, env: "
            "CONFORMANCE_CAPTURE_PUBLIC_URL). A wildcard bind has no address anything "
            "can connect to, so publishing it as the URL the target must be configured "
            "for is simply wrong. Pass the externally reachable /v1 base URL the target "
            "will use -- your tunnel, your VPS, or http://host.docker.internal:PORT/v1 "
            "for a container. A public bind also requires capture_token (CLI: "
            "--capture-token, env: CONFORMANCE_CAPTURE_TOKEN)."
        )
    if capture_mode == "public" and not capture_token:
        raise ValueError(
            "capture_token is required when the capture is reachable beyond loopback "
            "(CLI: --capture-token TOKEN, env: CONFORMANCE_CAPTURE_TOKEN -- prefer the "
            f"env var, process listings show argv). bind_host={capture_host!r}, "
            f"public_url={capture_public_url!r}. Without a token any internet traffic "
            "could enter the capture record. Configure the target's upstream API key to "
            "the same value so its requests are attributable."
        )

    # Per-run nonce. Without it nothing ties a captured request to THIS run, so a
    # target can exfiltrate raw PII to its real upstream and satisfy the boundary
    # check with one unrelated request to the capture server.
    nonce = _make_nonce()
    # Values vary per run; the FORMAT does not. A target that redacts by shape is
    # unaffected. See make_fixture() for why the card is drawn from a published list
    # instead of generated, and why an SSN an IPv4 could collide with is rejected.
    fixture = make_fixture()
    prompt = _build_prompt(nonce, fixture)

    probe_path = _PROBE_PATH_TEMPLATE.format(token=_make_probe_token())
    state = _CaptureState(probe_path, capture_token)

    # SO_REUSEADDR is left at HTTPServer's default on every platform. Clearing it on
    # Windows was tried and measured not to prevent a hijack -- the steal happens
    # when the two sockets bind DIFFERENT addresses, where the flag is irrelevant,
    # and the matching-address case was already refused without it. Keeping the flag
    # is also what lets a repeat run rebind over the previous run's TIME_WAIT
    # entries. The post-bind self-probe below is what actually fails closed here.
    server = ThreadingHTTPServer((capture_host, capture_port), _handler_for(state))
    actual_host, actual_port = server.server_address[:2]
    local_base_url = f"http://{actual_host}:{actual_port}/v1"
    # What the TARGET must be pointed at. In public mode that is the tester's own
    # externally reachable address, not the bind address -- a capture bound to
    # 0.0.0.0 has no usable URL of its own.
    advertised_base_url = (capture_public_url or local_base_url).rstrip("/")
    # The local probe must use an address the harness can actually connect to. A
    # wildcard bind is not one, so normalize it to loopback for the probe only.
    probe_host = "127.0.0.1" if actual_host in ("0.0.0.0", "::", "") else actual_host
    local_probe_url = f"http://{probe_host}:{actual_port}/v1{probe_path}"
    advertised_probe_url = advertised_base_url + probe_path
    effective_target = (
        advertised_base_url if target_base_url == "capture://self" else target_base_url
    )
    thread = threading.Thread(target=server.serve_forever, name="conformance-capture", daemon=True)
    thread.start()
    try:
        # Before any target traffic. A run whose capture cannot be reached measures
        # nothing, and the report it would otherwise emit is schema-valid and blames
        # the target.
        self_probe = _self_probe(
            local_probe_url,
            advertised_probe_url,
            probe_path,
            state,
            capture_token,
            min(timeout_seconds, 30.0),
        )
        exercise = asyncio.run(
            _exercise_target(
                effective_target,
                api_key,
                model,
                iterations,
                timeout_seconds,
                extra_headers or {},
                prompt,
                nonce,
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    all_records = state.snapshot()
    # The probe is the harness's own traffic. It is bucketed out of everything a
    # verdict is computed from -- captured_requests, correlation, and the leak
    # haystacks -- so it can neither pollute the record nor move a result. Its path
    # carries a per-run secret and contains no digits, so a target cannot address it
    # and it cannot contribute to a cross-request digit reassembly.
    target_records = [record for record in all_records if not record.get("probe")]
    # A public capture WILL receive traffic that is not the target's. It is recorded
    # and reported, never dropped, but it is not attributed to the target.
    captured = [record for record in target_records if record.get("authenticated", True)]
    unattributed = [record for record in target_records if not record.get("authenticated", True)]

    marker_words = nonce.split("-")
    leaked_types, leak_evidence, needle_proximity = _leaked_entities(captured, fixture)
    # Inspected SEPARATELY, not joined into the target's channels. Concatenating
    # anonymous internet traffic into the cross-request haystacks would let a third
    # party who knows the fixture and the capture URL manufacture a leak finding
    # against the measured gateway. Fixture values arriving at all still fail the check
    # -- unrelated traffic should not contain this synthetic fixture -- but the cause is
    # published in its own field so a reader is not told the target sent them.
    (
        unattributed_leaked_types,
        unattributed_leak_evidence,
        unattributed_needle_proximity,
    ) = _leaked_entities(unattributed, fixture)
    unattributed_uninspectable = [
        record for record in unattributed if not record["parsed"]
    ]
    # A conforming gateway may redact part of the marker, so a majority ties the
    # capture to this run. Requiring the literal nonce failed valid implementations.
    marker_hits = [_marker_matches(record, marker_words) for record in captured]
    correlated = [
        record
        for record, hits in zip(captured, marker_hits)
        if hits >= _NONCE_MIN_MATCHES
    ]
    uninspectable = [record for record in captured if not record["parsed"]]
    paths = sorted({str(record["path"]).split("?")[0] for record in captured})
    methods = sorted({str(record["method"]) for record in captured})
    latency = _percentiles(exercise["durations_ms"])
    checks = {
        "configured_upstream_boundary": {
            "passed": (
                bool(correlated)
                and not leaked_types
                and not unattributed_leaked_types
                and not uninspectable
            ),
            # A public capture is a weaker observation than a loopback one and a
            # reader must be able to tell which they are looking at without leaving
            # the check that made the claim.
            "capture_mode": capture_mode,
            "captured_requests": len(captured),
            "correlated_requests": len(correlated),
            "uninspectable_requests": len(uninspectable),
            # Recorded and published, never silently dropped. On a public capture a
            # non-zero count is expected background scan traffic and does NOT fail
            # the check: gating on it would fail a valid run for something a
            # stranger did. Fixture values inside it do fail, above.
            "unattributed_requests": len(unattributed),
            "unattributed_uninspectable_requests": len(unattributed_uninspectable),
            "unattributed_leaked_entity_types": unattributed_leaked_types,
            "unattributed_leak_evidence": unattributed_leak_evidence,
            "unattributed_needle_proximity": unattributed_needle_proximity,
            "unattributed_paths_observed": sorted(
                {str(record["path"]).split("?")[0] for record in unattributed}
            ),
            "uninspectable_reasons": sorted(
                {str(record["error"]) for record in uninspectable if record["error"]}
            ),
            "leaked_entity_types": leaked_types,
            # Where each finding came from and which matcher produced it. A bare
            # leaked_entity_types alone does not explain how a finding was produced;
            # "literal" means the fixture value appeared verbatim, "normalized" means
            # it was recovered only after joining and stripping separators.
            "leak_evidence": leak_evidence,
            # How close anything in the capture came to each protected value: the
            # longest contiguous run OF THE NEEDLE found in any haystack. Equal to the
            # needle length means it is present. Anything less is the margin -- round 6
            # measured 2 of 9 for the SSN on a validated loopback run. Published per run
            # because public capture mode lets tunnel-injected headers into the same
            # haystacks: a Cloudflare quick tunnel adds ten headers and took a measured
            # request from 20 digits to 93.
            "needle_proximity": needle_proximity,
            "needle_lengths": {
                entity: len(_normalize(value)) for entity, value in fixture.items()
            },
            "upstream_paths_observed": paths,
            "upstream_methods_observed": methods,
            "marker_words_required": _NONCE_MIN_MATCHES,
            "marker_words_total": len(marker_words),
            # How much of the marker actually survived into the best capture. Without
            # it, a gateway that correctly and reversibly masked the marker words (a
            # conforming detector may tag them) reports correlated_requests: 0 --
            # byte-identical to a target that exfiltrated elsewhere and sent decoy
            # traffic. The count separates "partly redacted" from "never arrived".
            "marker_words_observed_max": max(marker_hits) if marker_hits else 0,
            "inspection_scope": (
                "every HTTP/1.x request to the capture origin on any path or method: request "
                "line, headers, chunk extensions, trailers, and body, after transfer-encoding "
                "and content-encoding decoding, walked recursively over all JSON types, with "
                "base64/hex/percent-encoded runs and character-code arrays decoded, matched "
                "literally and with separators removed, including ordered per-channel joins "
                "across captured requests"
            ),
            "payload_content_included": False,
        },
        "fragmentation_safety": {
            "passed": exercise["text_matches"] and exercise["events"] > 1,
            "one_character_events_requested": True,
            "events_observed": exercise["events"],
            "events_observed_max": exercise["events_max"],
            # This check also gates on reconstruction, so it must publish that term.
            # Without it the check can report passed: false while events_observed
            # satisfies its own stated criterion and no field it emits is false --
            # an unattributable failed check in a third party's published report.
            "response_reconstructed": exercise["text_matches"],
            "coalescing_not_distinguished": True,
        },
        "sse_validity": {
            "passed": (
                exercise["invalid_events"] == 0
                and exercise["done_markers_valid"]
                and exercise["content_type_valid"]
                and not exercise["errors"]
            ),
            "invalid_events": exercise["invalid_events"],
            "done_markers_valid": exercise["done_markers_valid"],
            "content_type_valid": exercise["content_type_valid"],
            "status_codes": exercise["status_codes"],
            "errors": exercise["errors"],
        },
        "response_fidelity": {
            "passed": exercise["text_matches"],
            "expected_value_reconstructed": exercise["text_matches"],
            "iterations_matching": exercise["iterations_matching"],
            "iterations_completed": exercise["iterations_completed"],
            "iterations_requested": exercise["iterations_requested"],
            "payload_content_included": False,
        },
        "client_observed_latency": {
            "passed": len(exercise["durations_ms"]) == iterations,
            "threshold_enforced": False,
            "unit": "milliseconds",
            "iterations": iterations,
            # The gate is sample completeness, not latency. Publishing only the
            # requested count left every field of this check reading clean while it
            # reported passed: false -- reproduced against a gateway that returned
            # HTTP 429 on its own rate limit.
            "iterations_measured": len(exercise["durations_ms"]),
            **latency,
        },
    }
    attestation = build_attestation()
    measured_pass = all(check["passed"] for check in checks.values())
    # "Attributable" means at least one captured request carried this run's marker. A
    # run with none cannot tell "never configured" from "sent it elsewhere", which
    # round 6 established is not evidence of a leak -- so it is not a Fail either.
    outcome = derive_outcome(
        claim_block,
        passed=measured_pass,
        attributable=bool(correlated),
        # Only actual leak evidence makes a non-pass a leak finding. A one-way
        # anonymizer fails response_fidelity while leaking nothing.
        leaked=bool(leaked_types or unattributed_leaked_types),
    )
    report: dict[str, Any] = {
        "schema": "llm-shield.streaming-privacy-http-profile/v1.0.0",
        "generated_at": _timestamp(),
        "profile": {
            "name": "OpenAI-compatible HTTP gateway profile",
            "scope": "client-to-gateway request, controlled configured-upstream capture, and SSE response",
        },
        "implementation": {
            "name": implementation_name,
            "version": implementation_version,
            "labels_are_operator_supplied": True,
        },
        "target": {
            "base_url": target_base_url,
            "model": model,
            "extra_header_names": sorted(extra_headers or {}),
            "raw_pass_through_baseline": target_base_url == "capture://self",
        },
        # Dimensions only. Publishing the values would hand a shim the answer, and
        # publishing nothing left a reader unable to tell a varied-valid fixture from
        # the fixed-invalid one this replaced.
        "fixture": {
            "varies_per_run": True,
            "values_published": False,
            "formats": dict(PROTECTED_VALUE_FORMATS),
            "value_space_nominal": fixture_value_space(),
            "specimens_are_valid": (
                "Every value is a valid specimen a validating detector recognises: "
                "Luhn-valid 16-digit PAN, SSN with non-zero group and serial outside "
                "every ITIN group range, address on a real public suffix."
            ),
            "specimens_are_non_real": (
                "Reserved space only: example.com (RFC 2606 s3), the SSA "
                "never-issued 900-999 SSN area, and published test card numbers."
            ),
            "ssn_ipv4_collision_rejection": (
                "Generated SSNs whose digits any valid dotted-quad IPv4 address "
                "could produce are resampled, because header inspection puts client "
                "IPs into the same normalized haystack. A measured 37.3% of the "
                "nominal space is rejected on this rule, so the effective SSN space "
                "is roughly 3.1e7."
            ),
        },
        "harness_revision": os.getenv("GITHUB_SHA") or os.getenv("PII_LEAK_BENCHMARK_SOURCE_REVISION") or "unknown",
        "environment": {
            "python": platform.python_version(),
            "implementation": sys.implementation.name,
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
        "capture": {
            "mode": capture_mode,
            "bind_host": capture_host,
            "port": actual_port,
            "authentication_required": capture_token is not None,
            "target_must_be_preconfigured_for": advertised_base_url,
            # Proof the capture was reachable and recording at the address the target
            # was configured with, taken before any target traffic.
            "self_probe": self_probe,
        },
        "checks": checks,
        # The raw measurement: did all five checks pass. Never overwritten by the
        # claim logic, so a negative finding is preserved even when the outcome says
        # the run is not a verdict about the product.
        "passed": measured_pass,
        # What a published row is ALLOWED to say. Derived here, never operator-typed.
        "redaction_claim": claim_block,
        "outcome": outcome,
        "outcome_rationale": rationale_for(outcome),
        # Split deliberately. The old single list mixed two kinds of statement and a
        # reader had to wade through the permanent ones to reach the ones that decide
        # whether this particular artifact means anything. Only run_validity needs
        # reading before publishing a row.
        "limitations": {
            # B -- conditions that would make THIS run meaningless. Check every one
            # against the fields the report publishes before treating it as a result.
            "run_validity": [
                "The target must be configured to use the harness capture server as its "
                "upstream. This profile does not install or configure the target, and it "
                "cannot confirm the configuration took effect.",
                "Zero captured requests fails the boundary check but does not identify a "
                "cause. A target that was never configured to use the capture origin, or "
                "that cannot reach it, is indistinguishable here from one that sent the "
                "traffic somewhere else. Confirm the target's configured upstream before "
                "reading this as leak evidence.",
                "The capture server speaks plaintext HTTP/1.x, so the target must be able "
                "to open a plaintext HTTP connection to the advertised capture address. In "
                "loopback mode that address is on the tester's own machine; in public mode "
                "the tester supplies a reachable address and is assumed to terminate TLS at "
                "their own tunnel. A gateway that only egresses to an operator-fixed "
                "upstream it will not let the tester change cannot be measured by this "
                "check at all.",
                "capture.mode records which observation this was. In public mode the "
                "capture address is reachable by anyone, so it is a WEAKER observation "
                "than a loopback run: attribution rests on the capture token the target "
                "was configured with rather than on the address being unreachable from "
                "outside. Do not compare a public run and a loopback run as equivalent "
                "evidence.",
                "Requests that do not present the capture token are recorded and reported "
                "as unattributed, never dropped. A non-zero unattributed_requests on a "
                "public capture is expected background internet traffic and is not "
                "evidence about the target; unattributed_uninspectable_requests counts "
                "traffic the harness could not parse, which on a public address is "
                "ordinary noise. Protected fixture values appearing in unattributed "
                "traffic do fail the check and are reported separately, because a public "
                "capture address can be reached by a third party who knows the fixture.",
                "Authentication, rate-limit, blast-radius, and other gateway policy "
                "controls must permit the requested evaluation traffic; a policy "
                "rejection is a profile non-pass, not evidence that protected data leaked.",
                "Capture requests exceeding the byte, nesting, node, or decode budgets are "
                "failed closed as uninspectable; a non-pass from that condition is not "
                "evidence of a leak.",
                "The capture server observes HTTP/1.x. Unsupported or malformed protocol "
                "attempts are failed closed as uninspectable rather than decoded as HTTP/2 "
                "or HTTP/3, so a target that speaks only HTTP/2 upstream produces a "
                "non-pass that is about the harness, not about the target.",
                "leaked_entity_types names the entity but not the evidence. Read "
                "leak_evidence: a 'literal' match means the fixture value appeared "
                "verbatim in something the target sent; a 'normalized' match means it "
                "was recovered only after joining channels and stripping separators, "
                "which is what catches deliberate obfuscation but can in principle "
                "collide. needle_proximity reports how close anything came to each "
                "protected value, against needle_lengths -- equal means present. The "
                "margin is published per run because the values vary per run.",
                "In public mode the path between the target and the capture injects "
                "headers of its own, and they are inspected as an egress channel "
                "because a gateway could hide values there. A measured Cloudflare quick "
                "tunnel adds ten (cf-connecting-ip, x-forwarded-for, cf-ray and seven "
                "more) and takes a request from 20 digits to 93 without changing needle "
                "proximity. Any injected identifier whose digits happen to contain a "
                "protected value collides. The IPv4 case is now excluded by "
                "construction rather than disclosed: the generator rejects any SSN "
                "whose digits some valid dotted-quad address could produce, which is "
                "what made 123.45.67.89 normalize onto the previous fixed SSN and "
                "report a leak against a gateway that had redacted correctly. A random "
                "hex request id still reaches the needle at roughly 1e-10 per request, "
                "so leak_evidence records which matcher fired: check whether the match "
                "was literal before treating it as egress. The 16-digit card needle is "
                "unreachable by any IPv4.",
                "correlated_requests counts captured requests carrying at least the "
                "required number of marker words; marker_words_observed_max reports how "
                "much of the marker survived. A gateway that reversibly masks the marker "
                "can correlate zero requests while behaving correctly.",
            ],
            # A -- what this method can never see, in any run. Permanent.
            "method_limits": [
                "This HTTP profile does not evaluate gateway process RSS, audit evidence, "
                "or public-model behavior.",
                "The synthetic fixture does not establish population-level detector "
                "accuracy.",
                "The fixture VALUES vary per run but the FORMATS are fixed and "
                "published, so a target that matches the three shapes and substitutes "
                "them passes every check without operating a detector on anything "
                "else. Value variation raised the cheapest such shim from three string "
                "replacements to a working format matcher; it does not make one "
                "impossible. This profile measures the behaviour of the endpoint it "
                "was pointed at during the run; it does not establish that the "
                "endpoint is the product it is labelled as.",
                "The card value is drawn from a fixed list of published test PANs "
                "rather than generated. A generated Luhn-valid number in an issued BIN "
                "could be a live card, so the harness will not emit one -- which means "
                "the card carries less value entropy than the SSN and the email.",
                "Every fixture value is a VALID specimen: Luhn-valid card, an SSN in "
                "the never-issued 900-999 area with a non-ITIN group, and an address "
                "on the reserved example.com domain. A detector that validates its "
                "input therefore recognises all three. This replaced a fixture whose "
                "three values were all invalid specimens, which stock Presidio "
                "correctly ignored -- that fixture measured carefulness as a fault and "
                "favoured this project's own non-validating regex engine.",
                "Client-observed latency includes local HTTP and capture-server work and "
                "has no universal threshold.",
                "Implementation name and version are operator-supplied labels, not "
                "measured identity.",
                "Fragmentation safety verifies more than one event, which does not "
                "distinguish per-token streaming from a fully buffered response emitted "
                "as a few chunks.",
                "Any attestation block is self-reported run metadata, not third-party "
                "verification.",
                "Leak detection covers the configured capture origin only. Egress to any "
                "other destination is outside what this profile can observe.",
                "The observation window ends after the client iterations finish; egress "
                "deliberately deferred until after capture shutdown is outside this "
                "finite run.",
                "The profile inspects HTTP application data, not covert encodings in "
                "request counts, ordering, timing, connection metadata, packetization, or "
                "DNS/TLS metadata.",
                "Inspection recovers encoded and fragmented values within a request and "
                "ordered fragments in the same channel across requests, but does not "
                "reassemble arbitrary fragments separated by unrelated values or moved "
                "between channel types.",
            ],
        },
    }
    if attestation is not None:
        report["attestation"] = attestation
    return report
