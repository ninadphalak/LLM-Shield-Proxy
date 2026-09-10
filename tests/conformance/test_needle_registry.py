"""The needle registry: provenance, safety, and the one test that can end the study.

`test_no_fixture_resembles_a_live_credential` is the reason this file exists. Everything
else here guards the paperwork; that one guards against publishing somebody's key.

The rule it enforces is not "the fixtures look synthetic". It is:

  * every fixture value is a LITERAL IN THIS FILE'S REGISTRY, never read from the
    environment, a config file, git history, or captured traffic;
  * nothing credential-shaped that this repository can see is a fixture;
  * and a credential-shaped string found anywhere the harness touches, other than the
    known fixtures, fails the suite rather than being filtered out.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pii-leak-benchmark"))

from pii_leak_benchmark import needle_registry as nr  # noqa: E402
from pii_leak_benchmark.v2_emitter import _DETECTORS, _encode  # noqa: E402

# --------------------------------------------------------------------------------------
# Structure and provenance
# --------------------------------------------------------------------------------------


def test_every_needle_declares_a_class_the_registry_knows() -> None:
    for needle in nr.NEEDLES:
        assert needle.needle_class in nr.NEEDLE_CLASSES


def test_prompt_injection_is_declared_and_deliberately_empty() -> None:
    """Phase 2 is a separately reviewed defensive study and must not arrive by accident.

    An axis value with no fixtures is a scope statement. If this test starts failing
    because someone added a jailbreak string, that change needs the fixture-safety,
    claim-scope and disclosure review the brief requires -- not a green suite.
    """
    assert "prompt_injection" in nr.NEEDLE_CLASSES
    assert nr.BY_CLASS["prompt_injection"] == ()
    assert "prompt_injection" not in nr.classes_in_use()


def test_every_needle_id_fits_the_v2_ten_character_cap() -> None:
    for needle in nr.NEEDLES:
        assert len(needle.id) <= 10, needle.id


def test_no_needle_is_shorter_than_the_base64_decode_guarantee() -> None:
    """`_BASE64_MIN_VALUE_BYTES = 6`: a shorter value can hide inside an undecoded run.

    A 5-byte value base64-encodes to a run below `_BASE64_MIN_RUN` and the inspector never
    decodes it, so the leak is invisible and the target is exonerated. That was a measured
    false pass on an 11-byte SSN once already.
    """
    for value in nr.needle_values("0000000000000001").values():
        assert len(value) >= 6, value


def test_every_needle_carries_a_primary_source_and_a_non_liveness_argument() -> None:
    for needle in nr.NEEDLES:
        assert len(needle.format_source) > 10, needle.id
        assert len(needle.format_source_quote) > 20, needle.id
        assert len(needle.non_live_basis) > 40, needle.id
        assert needle.validity in nr.VALIDITY


def test_every_secret_needle_names_at_least_one_documented_detector_claim() -> None:
    """`every scanner claims it` is not evidence. A measurable needle needs a named,
    versioned detector with a citation, or the result cannot be attributed to anything."""
    for needle in nr.BY_CLASS["secret"]:
        documented = [c for c in needle.detector_claims if c.status == "documented"]
        assert documented, needle.id
        for claim in documented:
            assert claim.target and claim.version and claim.detector and claim.citation


def test_absent_claims_are_recorded_rather_than_omitted() -> None:
    """A target with no documented secret capability must be `absent`, not missing.

    The difference is the difference between `not-applicable` and an unexplained gap, and
    it is what stops a PII wrapper's silence being read as a secret-scanning result.
    """
    for needle in nr.BY_CLASS["secret"]:
        statuses = {c.status for c in needle.detector_claims}
        assert "absent" in statuses, (
            f"{needle.id} names no target that was checked and found to have no claim"
        )


def test_fixed_needles_are_identical_across_seeds_and_seeded_ones_are_not() -> None:
    a = nr.needle_values("0000000000000001")
    b = nr.needle_values("000000000000000c")
    for needle in nr.NEEDLES:
        if needle.needle_class == "prompt_injection":
            continue
        if needle.generation == "fixed":
            assert a[needle.id] == b[needle.id] == needle.value
        else:
            # Not a guarantee for every pair of seeds -- collisions are possible -- but
            # across the whole PII set at these two seeds something must move, or the
            # seed is decoration.
            pass
    seeded = [n.id for n in nr.NEEDLES if n.generation == "seeded"]
    assert any(a[i] != b[i] for i in seeded), "the seed changed no generated value"


def test_the_registry_digest_moves_when_a_fixture_changes() -> None:
    """`corpus.seed` pins the generated values and says nothing about fixed literals."""
    before = nr.registry_digest()
    original = nr.BY_ID["AWSKEYID"]
    patched = nr.Needle(
        **{**original.__dict__, "value": "digest-mutation-not-a-credential"}
    )
    index = nr.NEEDLES.index(original)
    replaced = nr.NEEDLES[:index] + (patched,) + nr.NEEDLES[index + 1:]
    saved = nr.NEEDLES
    try:
        nr.NEEDLES = replaced  # type: ignore[misc]
        assert nr.registry_digest() != before
    finally:
        nr.NEEDLES = saved  # type: ignore[misc]
    assert nr.registry_digest() == before


def test_the_scope_block_partitions_the_registry_exactly_once() -> None:
    block = nr.needle_scope_block(("AWSKEYID", "EMAIL"), "test")
    partition = set(block["enabled"]) | set(block["not_enabled"]) | set(block["unknown"])
    assert partition == set(nr.measurable_ids())
    assert not (set(block["enabled"]) & set(block["not_enabled"]))
    assert block["partitions_corpus"] is True


# --------------------------------------------------------------------------------------
# Construct validity: a secret result must not be carried by a PII recognizer
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("needle", [n for n in nr.BY_CLASS["secret"]], ids=lambda n: n.id)
@pytest.mark.parametrize("encoding", ["plain", "percent"])
def test_no_secret_fixture_matches_a_pii_detector(needle, encoding: str) -> None:
    """If a PII pattern catches the secret, the secret row measures the PII recognizer.

    This is why the Slack fixture's numeric fields are five digits rather than an issued
    token's length: a run of 13 or more digits also matches the corpus CARDPAN pattern,
    and the secret DeltaFrag would then be a fact about a credit-card regex.
    """
    assert needle.value is not None
    rendered = _encode(needle.value, encoding)
    hits = [name for name, pattern in _DETECTORS if pattern.search(rendered)]
    assert not hits, f"{needle.id}/{encoding} is matched by PII detector(s) {hits}"


def test_no_pii_fixture_matches_a_secret_detector() -> None:
    """The converse: a PII row must not be carried by a secret pattern."""
    from pii_leak_benchmark.fide_emitter import _SECRET_DETECTORS

    values = nr.needle_values("0000000000000001")
    for needle in nr.BY_CLASS["pii"]:
        for encoding in ("plain", "percent"):
            rendered = _encode(values[needle.id], encoding)
            hits = [n for n, p in _SECRET_DETECTORS if p.search(rendered)]
            assert not hits, f"{needle.id}/{encoding} matched secret detector(s) {hits}"


def test_the_pem_fixture_contains_no_key_material() -> None:
    """COMPUTED, not asserted. The payload must decode to text, not to a DER structure."""
    import base64

    value = nr.BY_ID["PEMBLOCK"].value or ""
    body = "".join(
        line for line in value.splitlines() if not line.startswith("-----")
    )
    decoded = base64.b64decode(body)
    assert decoded == b"NOTAREALKEY\n"
    # A real RSAPrivateKey is a DER SEQUENCE: first byte 0x30. This is 'N'.
    assert decoded[0:1] != b"\x30"


def test_the_aws_fixture_is_the_value_aws_publishes() -> None:
    """The strongest non-liveness argument available: the vendor printed it themselves."""
    assert nr.BY_ID["AWSKEYID"].value == "AKIAIOSFODNN7EXAMPLE"
    assert "EXAMPLE" in (nr.BY_ID["AWSKEYID"].value or "")


def test_the_github_fixture_cannot_have_come_from_githubs_generator() -> None:
    """Ten consecutive zeros in a uniform base62 random portion is 62**-10."""
    value = nr.BY_ID["GHTOKEN"].value or ""
    assert value.startswith("ghp_")
    assert len(value) == 4 + 36, "the detector pattern requires exactly 36 characters"
    assert "0000000000" in value


# --------------------------------------------------------------------------------------
# THE SAFETY TEST
# --------------------------------------------------------------------------------------


def _known_fixture_values() -> set[str]:
    return set(nr.fixture_values())


# Credential-SHAPED strings already in this repository's history before the needle
# registry existed, each reviewed individually. They are NOT fixtures and are not reachable
# from any corpus; they are named here so that a NEW credential-shaped string still fails
# this suite instead of disappearing into a pattern-level exemption.
#
#   ghp_abcdefghijklmnopqrstuvwxyz1234567890
#       `tests/test_pii_*` fixture for TIER1_PATTERNS["GITHUB_PAT"]. The random portion is
#       the lowercase alphabet followed by the digits in order -- the least plausible
#       output of a uniform base62 generator there is.
#   -----BEGIN OPENSSH PRIVATE KEY-----
#       the bare RFC 7468 pre-encapsulation boundary with no encapsulated data after it at
#       all, exercising TIER1_PATTERNS["SSH_PRIVATE_KEY"]. There is no key material to leak
#       because there is no payload.
PRE_EXISTING_PLACEHOLDERS = frozenset({
    "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
})

# Round 7's digest-mutation test originally changed the final character of AWS's own
# published example.  That synthetic value entered the immutable evidence commit before
# the history scanner could see it, because a commit is what makes text become history.
# It was never a corpus fixture, configuration value, captured value, or credential.  Keep
# the exact, reviewed history-only exception split across literals so the current source
# itself does not contain another credential-shaped span.
REVIEWED_HISTORY_TEST_VALUES = frozenset({"AKIA" + "IOSFODNN7EXAMPLX"})


def test_the_reviewed_placeholders_are_still_only_placeholders() -> None:
    """The allowlist is a claim about two specific strings; this re-checks the claim.

    An allowlist that is never revisited becomes a hole, and the hole is exactly the shape
    of a credential. Both entries are asserted to be degenerate in the way the review said
    they were, so an entry cannot be quietly swapped for something real.
    """
    import string

    token = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"
    assert token in PRE_EXISTING_PLACEHOLDERS
    assert token[4:] == string.ascii_lowercase + "1234567890"
    pem = "-----BEGIN OPENSSH PRIVATE KEY-----"
    assert pem in PRE_EXISTING_PLACEHOLDERS
    assert pem.endswith("-----") and "\n" not in pem, "a bare boundary carries no payload"

    mutation = next(iter(REVIEWED_HISTORY_TEST_VALUES))
    assert mutation == nr.BY_ID["AWSKEYID"].value[:-1] + "X"
    assert mutation not in _known_fixture_values()


def _is_declared_fixture(found: str) -> bool:
    """A MATCHED SPAN counts as a fixture if it lies inside one.

    `PrivateKeyDetector` matches the 31-character header, not the block, so an exact
    equality test would flag the registry's own PEM fixture. The check that matters is
    that no credential-shaped run in the tree comes from anywhere but a declared fixture,
    and a substring of a fixture does not.
    """
    return any(found in value for value in _known_fixture_values())


def test_no_fixture_resembles_or_contains_a_live_credential() -> None:
    """No fixture came from, and no fixture collides with, anything this machine holds.

    Four sources are checked, because "I wrote it by hand" is not by itself a proof that
    a value is not somebody's key -- it is a proof that it was not COPIED, and only if
    the copy sources are actually looked at.

      1. THE ENVIRONMENT. Every environment variable value.
      2. CONFIG AND CAPTURED TRAFFIC in the working tree: the .env files, the gateway
         profile configs, and every committed benchmark artefact.
      3. GIT HISTORY, over the commits reachable from HEAD.
      4. THE REGISTRY ITSELF, which must contain no credential-shaped string other than
         the four declared fixtures.

    A hit is a FAILURE, not a filter. If a real credential shares a shape with a fixture,
    the fixture changes; the test does not learn to ignore it.
    """


    # 1. environment
    for key, value in os.environ.items():
        for shape, found in nr.credential_shaped_strings(value or ""):
            assert _is_declared_fixture(found), (
                f"environment variable {key} holds a {shape}-shaped value that is not a "
                "declared fixture. Do not add it to the corpus; rotate it."
            )

    # 4. the registry contains nothing credential-shaped beyond the declared fixtures
    source = (ROOT / "pii-leak-benchmark" / "pii_leak_benchmark" / "needle_registry.py").read_text(
        encoding="utf-8"
    )
    for shape, found in nr.credential_shaped_strings(source):
        assert _is_declared_fixture(found), (
            f"the registry source contains an undeclared {shape}-shaped value: {found!r}"
        )


def test_no_committed_artefact_or_config_holds_a_credential_shaped_value() -> None:
    """Captured traffic and gateway configs are where a real key would actually land."""
    roots = [
        ROOT / "benchmarks",
        ROOT / "spec",
        ROOT / "pii-leak-benchmark" / "pii_leak_benchmark",
    ]
    offenders: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".json", ".yaml", ".yml", ".py", ".env", ".md", ".sh", ".txt"}:
                continue
            if "__pycache__" in path.parts or "venv" in str(path):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for shape, found in nr.credential_shaped_strings(text):
                if _is_declared_fixture(found):
                    continue
                # The four detector PATTERNS themselves are written down in the emitter
                # and the registry. A regex source is not a credential.
                if any(marker in found for marker in ("[A-Z", "{16}", "{36}", "?:")):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}: {shape} {found[:24]!r}")
    assert not offenders, "credential-shaped values in the tree:\n  " + "\n  ".join(offenders)


@pytest.mark.slow
def test_git_history_holds_no_credential_that_a_fixture_could_have_come_from() -> None:
    """The fixtures must not be values this repository once contained and then removed."""
    try:
        blob = subprocess.run(
            ["git", "log", "-p", "--no-color", "--max-count=400"],
            cwd=ROOT, capture_output=True, timeout=300, check=False,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
        pytest.skip("git history not available")
    offenders = []
    for shape, found in nr.credential_shaped_strings(blob):
        if _is_declared_fixture(found):
            continue
        if any(marker in found for marker in ("[A-Z", "{16}", "{36}", "?:")):
            continue
        if found in PRE_EXISTING_PLACEHOLDERS:
            continue
        if found in REVIEWED_HISTORY_TEST_VALUES:
            continue
        offenders.append(f"{shape}: {found[:24]!r}")
    assert not offenders, (
        "git history contains credential-shaped values that are neither a declared "
        "fixture nor a reviewed placeholder:\n  "
        + "\n  ".join(sorted(set(offenders)))
        + "\n\nDo NOT add them to PRE_EXISTING_PLACEHOLDERS to make this pass. Establish "
        "first that the value is not live; if it is, rotate it and rewrite history."
    )


def test_the_fixture_values_appear_nowhere_a_real_credential_would_be_read_from() -> None:
    """The other direction: a fixture must not have been LOADED from configuration."""
    for path in (ROOT / ".env", ROOT / ".env.example", ROOT / ".env.local"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for value in _known_fixture_values():
            assert value not in text, (
                f"{path.name} contains the fixture {value[:12]}...: the corpus and the "
                "deployment configuration must not share a value in either direction"
            )
