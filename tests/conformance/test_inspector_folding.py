"""Two measured false passes in the capture-side inspector, and the tests that pin them.

Both defects had the same shape: an encoding the capture server could not decode, so
the fixture value reached the upstream and the report said no leak. That is the worst
failure this harness has, because it exonerates a gateway that leaked.

DEFECT 1 -- the base64 run floor. ``_BASE64ISH`` required 16 characters before a run
was worth decoding. Base64 of an n-byte value is ceil(4n/3) characters, so an 11-byte
value -- exactly a ``ddd-dd-dddd`` SSN -- encodes to 15 and was never inspected.
Measured end to end against the existing ``double_base64`` evasion gateway, whose
outer layer decodes to precisely that 15-character inner run::

    prefixed_base64 -> leaked: [CREDIT_CARD, EMAIL, SSN]   SSN proximity 9/9
    double_base64   -> leaked: [CREDIT_CARD, EMAIL]        SSN proximity 2/9

DEFECT 2 -- ``_normalize`` deleted non-ASCII instead of folding it. ``[^0-9a-z]``
removes a character it does not recognise, so a fullwidth SSN normalized to the empty
string and one Cyrillic ER inside ``jrmccalx@example.com`` normalized to
``jrmccalxexamlecom``. Two of the corpus's Axis B encodings (``homoglyph``, and the
fullwidth neighbour of ``numeric-char-array``) were therefore unmeasurable.

WHY NEITHER WAS CAUGHT. Every existing leak test scores a run carrying all three
fixture values, and one leaked entity fails a run. The email is 20 bytes and survives
both defects, so the run failed for the right reason with the wrong evidence and no
assertion noticed. The corpus puts ONE entity in a case. There is no second entity to
fail on, and the same request reads as a clean pass -- so the single-entity end-to-end
test at the bottom of this file is the one that actually had to exist.

The other direction matters as much: a fold that is too eager invents leaks. Folding
ASCII ``o`` to ``0`` across a joined capture manufactures digit runs nothing sent,
which is the round 7 IPv4 false positive again. ``test_ascii_input_normalizes_exactly
_as_it_did_before`` and ``test_non_ascii_prose_does_not_manufacture_a_finding`` are
the guards on that side.
"""

import base64
import json
import re
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pii_leak_benchmark.confusables import (
    CONFUSABLE_TO_ASCII,
    CONFUSABLE_TO_DIGIT,
)
from pii_leak_benchmark.http_profile import (
    _BASE64_MIN_RUN,
    _BASE64_MIN_VALUE_BYTES,
    PROTECTED_ENTITY_TYPES,
    _collect,
    _Inspection,
    _normalize,
    _normalize_confusable_digits,
    extract_fixture,
    run_http_conformance,
)

# ---------------------------------------------------------------------------
# Encoders. Each one is a transport representation the upstream still parses back
# into the protected value, so each one must be recoverable by the inspector.
# ---------------------------------------------------------------------------

# Derived from the vendored table rather than hand-picked, so this exercises the
# shipped fold instead of a parallel opinion about which glyphs look alike. Lowest
# codepoint per target keeps it deterministic across regenerations.
_LOOKALIKE = {}
for _source, _target in sorted(CONFUSABLE_TO_DIGIT.items()):
    _LOOKALIKE.setdefault(_target, _source)
for _source, _target in sorted(CONFUSABLE_TO_ASCII.items()):
    _LOOKALIKE.setdefault(_target, _source)


def _homoglyph(value):
    """Every ASCII alphanumeric replaced by a non-ASCII look-alike."""
    return "".join(_LOOKALIKE.get(character, character) for character in value.lower())


def _fullwidth(value):
    """Halfwidth to fullwidth forms -- reached by NFKD, not by the fold table."""
    out = []
    for character in value:
        code = ord(character)
        if "0" <= character <= "9":
            out.append(chr(0xFF10 + code - 0x30))
        elif "a" <= character.lower() <= "z":
            out.append(chr(0xFF41 + ord(character.lower()) - 0x61))
        else:
            out.append(character)
    return "".join(out)


def _devanagari_digits(value):
    """Digits in a non-Latin script -- reached by unicodedata.decimal only."""
    return "".join(
        chr(0x0966 + ord(character) - 0x30) if character.isdigit() else character
        for character in value
    )


def _bare_base64(value):
    """No prefix. A single leading character was the only reason defect 1 ever
    produced a finding: it padded the run from 15 characters up to 16."""
    return base64.b64encode(value.encode()).decode()


def _double_base64(value):
    return base64.b64encode(base64.b64encode(value.encode())).decode()


# ``needs_digits`` marks encoders that are a no-op on a value with no digits in it.
# Applying one to the email would hand the capture the plain value and the case would
# assert nothing.
ENCODERS = {
    "bare_base64": (_bare_base64, False),
    "double_base64": (_double_base64, False),
    "homoglyph": (_homoglyph, False),
    "fullwidth": (_fullwidth, False),
    "devanagari_digits": (_devanagari_digits, True),
}


def _applicable(encoder_name, value):
    _, needs_digits = ENCODERS[encoder_name]
    return not needs_digits or any(character.isdigit() for character in value)


def _recovers(value, encoded):
    """Would the inspector find ``value`` in a body carrying ``encoded``?

    Runs the real walk over a real JSON shape rather than calling the fold directly,
    so a decoder that works in isolation but is never reached still fails.
    """
    found = _Inspection()
    _collect({"payload": encoded}, found)
    # Both streams, exactly as `_leaked_entities` searches them: decoded material is
    # kept out of the ordered verbatim stream so it cannot split a fragment pair.
    seen = found.strings + found.decoded_strings
    needle, digit_needle = _normalize(value), _normalize_confusable_digits(value)
    joined = "".join(seen)
    return (
        value in seen
        or needle in _normalize(joined)
        or digit_needle in _normalize_confusable_digits(joined)
    )


# ---------------------------------------------------------------------------
# Defect 1: the base64 run floor
# ---------------------------------------------------------------------------


def test_the_documented_base64_guarantee_holds_for_its_stated_floor():
    """The floor and the guarantee must not drift apart.

    ``_BASE64_MIN_VALUE_BYTES`` is a published promise -- no corpus entity may be
    shorter -- so it has to follow arithmetically from the run length actually
    required, not be a comment that once matched.
    """
    encoded_length = -(-4 * _BASE64_MIN_VALUE_BYTES // 3)
    assert encoded_length >= _BASE64_MIN_RUN
    shorter = -(-4 * (_BASE64_MIN_VALUE_BYTES - 1) // 3)
    assert shorter < _BASE64_MIN_RUN, "the floor promises more than it can deliver"


@pytest.mark.parametrize("length", range(_BASE64_MIN_VALUE_BYTES, 24))
def test_bare_base64_of_any_value_at_the_floor_or_above_is_decoded(length):
    value = "".join("abcdefghij"[index % 10] for index in range(length))
    assert _recovers(value, _bare_base64(value))


def test_the_eleven_byte_value_that_defect_one_hid():
    """The regression itself: an SSN is 11 bytes and base64s to a 15-character run.

    Both layers are asserted. ``double_base64`` is the shape the existing evasion
    suite already sent and still missed, because its outer layer decodes to exactly
    the bare 15-character run this test's first assertion covers.
    """
    ssn = "914-27-6083"
    assert len(ssn.encode()) == 11
    assert len(_bare_base64(ssn).rstrip("=")) == 15
    assert _recovers(ssn, _bare_base64(ssn))
    assert _recovers(ssn, _double_base64(ssn))


# ---------------------------------------------------------------------------
# Defect 2: folding, in both directions
# ---------------------------------------------------------------------------


def test_ascii_input_normalizes_exactly_as_it_did_before():
    """The pre-fix expression, kept verbatim as the oracle.

    Nothing about ASCII may change. Every published run and every false-positive
    argument this project has made rests on it, and the fold's whole safety case is
    that it cannot touch ASCII.
    """
    previous = lambda text: re.sub(r"[^0-9a-z]+", "", text.lower())  # noqa: E731
    samples = [
        "Synthetic conformance record bracket-gravel-kettle-pantry-socket",
        "contact jrmccalx@example.com, SSN 914-27-6083, card 4111-1111-1111-1111",
        "data: {\"choices\":[{\"delta\":{\"content\":\"x\"}}]}",
        "",
        "0123456789",
    ]
    for sample in samples:
        assert _normalize(sample) == previous(sample)
        assert _normalize_confusable_digits(sample) == previous(sample)


def test_the_confusables_table_never_rewrites_an_ascii_character():
    for table in (CONFUSABLE_TO_ASCII, CONFUSABLE_TO_DIGIT):
        assert not [source for source in table if source.isascii()]
        assert all(len(target) == 1 and target.isascii() for target in table.values())
    assert set(CONFUSABLE_TO_DIGIT.values()) == {"0", "1"}
    assert set(CONFUSABLE_TO_DIGIT) <= set(CONFUSABLE_TO_ASCII)


@pytest.mark.parametrize("table_name", ["CONFUSABLE_TO_ASCII", "CONFUSABLE_TO_DIGIT"])
def test_every_vendored_row_actually_fires(table_name):
    """A dead row is worse than a missing one: it reads as coverage that is not there.

    Two whole classes of dead row were found by asserting this rather than by
    reasoning about it. SCRIPT CAPITAL I decomposes under NFKD to ASCII ``I``, so the
    fold takes its ASCII fast path and the table is never consulted. BENGALI DIGIT
    FOUR is listed in UTS #39 as confusable with ``8``, and the fold answers ``4``
    because ``unicodedata.decimal`` runs first and is right to. Both are now excluded
    by the generator, and this is what holds that line if the source revision moves.

    It also pins the ordering bug this file exists to prevent recurring: case folding
    before the lookup silently deleted every uppercase source row, and nothing else
    here would have failed.
    """
    table = {"CONFUSABLE_TO_ASCII": CONFUSABLE_TO_ASCII, "CONFUSABLE_TO_DIGIT": CONFUSABLE_TO_DIGIT}[
        table_name
    ]
    fold = _normalize if table_name == "CONFUSABLE_TO_ASCII" else _normalize_confusable_digits
    dead = {source: expected for source, expected in table.items() if fold(source) != expected}
    assert not dead, f"{len(dead)} rows never fire, e.g. {list(dead.items())[:5]}"


def test_a_decimal_digit_beats_its_look_alike():
    """UTS #39 calls DEVANAGARI DIGIT ZERO confusable with the letter ``o`` and
    BENGALI DIGIT FOUR confusable with ``8``. For a needle made of digits the UCD's
    decimal value is the truth, whatever the glyph resembles.

    Two independent guards, and both matter. The generator drops every source that
    carries a decimal value, so no such row can be shipped; and ``_fold`` consults
    ``unicodedata.decimal`` before the table, so a row that somehow arrived could not
    win. Deleting either one alone leaves the other holding.
    """
    import unicodedata

    for table in (CONFUSABLE_TO_ASCII, CONFUSABLE_TO_DIGIT):
        assert not [
            source for source in table if unicodedata.decimal(source, None) is not None
        ]
    for digits, expected in (("०१२", "012"), ("৪", "4"), ("٩١٤", "914")):
        assert _normalize(digits) == expected
        assert _normalize_confusable_digits(digits) == expected


def test_deletion_was_the_defect_and_folding_is_the_fix():
    """The two measured strings, byte for byte."""
    assert _normalize("９１４-２７-６０８３") == "914276083"
    assert _normalize("jrmccalx@examрle.com") == "jrmccalxexamplecom"
    # A precomposed accent used to cost its whole letter.
    assert _normalize("josé") == "jose"


def test_the_zero_family_needs_the_second_fold():
    """A Cyrillic o in place of a zero. UTS #39's prototype for that family is the
    LETTER o, so the primary fold alone cannot match a needle containing a zero --
    which is the entire reason there is a second haystack."""
    disguised = "914-27-6о83"
    assert _normalize(disguised) == "914276o83"
    assert _normalize_confusable_digits(disguised) == "914276083"


@pytest.mark.parametrize("encoder_name", sorted(ENCODERS))
@pytest.mark.parametrize("entity", PROTECTED_ENTITY_TYPES)
def test_every_encoder_round_trips_through_the_inspector(encoder_name, entity):
    from pii_leak_benchmark.http_profile import REFERENCE_FIXTURE

    value = REFERENCE_FIXTURE[entity]
    if not _applicable(encoder_name, value):
        pytest.skip(f"{encoder_name} is a no-op on {entity}")
    encode, _ = ENCODERS[encoder_name]
    assert _recovers(value, encode(value))


def test_non_ascii_prose_does_not_manufacture_a_finding():
    """The false-positive direction. A gateway that answers in Russian or Greek folds
    to a lot of ASCII letters and, in the second haystack, to a lot of zeros and ones.
    None of that may produce a nine-digit needle that was never sent."""
    prose = (
        "Привет, это ответ "
        "шлюза. Κалημέρα "
        "κόσμε. " * 40
    )
    for fold in (_normalize, _normalize_confusable_digits):
        folded = fold(prose)
        assert not re.search(r"\d{9}", folded), folded[:80]


# ---------------------------------------------------------------------------
# The single-entity end-to-end case. This is the test that had to exist.
# ---------------------------------------------------------------------------


def _free_port():
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def capture_port():
    return _free_port()


def _single_entity_gateway(encoder_name, entity, capture_port):
    """A gateway that hides EXACTLY ONE value, in one encoding, and nothing else.

    Every fixture value is masked out of the visible message, so there is no second
    entity to fail the run. If the inspector cannot decode this one, the report says
    the gateway was clean -- which is what both defects did.
    """
    encode, _ = ENCODERS[encoder_name]
    upstream = f"http://127.0.0.1:{capture_port}/v1/chat/completions"

    class SingleEntityGateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            prompt = json.loads(self.rfile.read(length))["messages"][-1]["content"]
            fixture = extract_fixture(prompt)
            masked = prompt
            for index, value in enumerate(fixture.values()):
                masked = masked.replace(value, f"[TOK_{index}]")
            body = json.dumps(
                {
                    "model": "m",
                    "messages": [{"role": "user", "content": masked}],
                    "trace": encode(fixture[entity]),
                }
            ).encode()
            request = urllib.request.Request(
                upstream, data=body, headers={"content-type": "application/json"}
            )
            try:
                urllib.request.urlopen(request, timeout=30).read()
            except urllib.error.HTTPError as exc:
                exc.read()

            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            for character in prompt:
                event = {"choices": [{"delta": {"content": character}}]}
                self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return SingleEntityGateway


def _run(handler_class, port):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        return run_http_conformance(
            f"http://127.0.0.1:{server.server_address[1]}/v1",
            iterations=1,
            capture_port=port,
        )
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("encoder_name", sorted(ENCODERS))
@pytest.mark.parametrize("entity", PROTECTED_ENTITY_TYPES)
def test_a_single_entity_leak_is_detected_with_no_neighbour_to_fail_on(
    encoder_name, entity, capture_port
):
    from pii_leak_benchmark.http_profile import REFERENCE_FIXTURE

    if not _applicable(encoder_name, REFERENCE_FIXTURE[entity]):
        pytest.skip(f"{encoder_name} is a no-op on {entity}")
    report = _run(_single_entity_gateway(encoder_name, entity, capture_port), capture_port)
    boundary = report["checks"]["configured_upstream_boundary"]
    assert boundary["leaked_entity_types"] == [entity], (
        f"{encoder_name} hid {entity} from the inspector: {boundary['leaked_entity_types']}, "
        f"proximity {boundary['needle_proximity']} of {boundary['needle_lengths']}"
    )
    assert boundary["passed"] is False
    assert report["passed"] is False
