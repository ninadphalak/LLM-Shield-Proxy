"""Axis A for the FIDE profile: the needles, CLASSED, with their provenance.

WHY THIS IS NOT `entity_registry.py`. That registry is a list of 72 personal-data entity
types with GDPR/DPDP/NIST/HIPAA/PCI dispositions. A credential is not a personal-data
entity type, and adding `AWS access key` to it as though it were would corrupt the one
thing that registry is for -- the claim that its 72 rows are derived from named
data-protection instruments. The disposition IDEA is reused; the taxonomy is not.

WHAT A NEEDLE IS. A needle is a value the profile injects into a response and expects a
target to withhold. `needle_class` says what KIND of thing it is, and it is an explicit
axis because Fragmentation-Induced Detection Evasion is not intrinsically a PII failure:
the transport oracle does not know or care what the injected bytes mean. Making the class
machine-readable is what stops a cross-class comparison from being published on the basis
of a directory name.

THE FIVE THINGS EVERY FIXTURE MUST CARRY, and none of them is optional:

  1. A PRIMARY SOURCE for the documented format, retrieved and quoted -- not another
     document's summary of it. This project has twice been damaged by citing a summary
     (`propagation-history.md`, ledger G12).
  2. A PROOF OF NON-LIVENESS that does not reduce to "it looks fake". Every claim below
     is either published by the vendor as an example, or computed here.
  3. A VALIDITY DISPOSITION: syntactically valid, deliberately invalid, or merely
     shape-valid. "It matches the regex" and "it would authenticate" are different
     statements and a fixture that conflates them is a live-credential incident waiting
     to happen.
  4. A DOCUMENTED DETECTOR CLAIM, naming the detector and the version, so a
     `not-applicable` row can be told from a miss. "Every scanner claims it" is not
     evidence.
  5. A COMPLETE-UNIT BASELINE requirement. A target that misses the whole value cannot
     demonstrate fragmentation evasion for that fixture; it demonstrates blindness, which
     is a different result. The emitter enforces this through `detector_blind_entities`.

SAFETY RULES THAT ARE NOT NEGOTIABLE. No fixture is ever drawn from the environment, a
config file, git history, or captured traffic; `assert_no_fixture_resembles_a_live_secret`
is the test that proves it. No fixture is ever presented to the issuing service to see
whether it works: submitting a credential-shaped string to an authentication endpoint is
using a credential, whatever its provenance.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from typing import Any

from .v2_emitter import make_seeded_fixture

# The classes the profile knows. `prompt_injection` is DECLARED and DELIBERATELY EMPTY:
# it is a separately reviewed defensive phase, and an axis value with no fixtures behind
# it is an honest statement of scope. It must not be populated in the same change that
# lands the secret family.
NEEDLE_CLASSES: tuple[str, ...] = ("pii", "secret", "prompt_injection")

# Validity dispositions. These are about the VALUE, not about whether a detector finds it.
VALIDITY = (
    # Conforms to the documented format in every respect the primary source states.
    "syntactically-valid",
    # Conforms to the documented *shape* but violates a documented validity constraint on
    # purpose (a checksum, a reserved range, an assignment rule).
    "deliberately-invalid",
    # Matches the shape a detector looks for; the primary source does not fully specify a
    # validity rule, so no stronger claim is made.
    "shape-valid",
)


@dataclass(frozen=True)
class DetectorClaim:
    """One target's DOCUMENTED claim to find this needle class. Not a measurement."""

    target: str
    version: str
    # The named detector/plugin/infoType, so the claim is checkable at that granularity.
    detector: str
    # Where the claim is written down.
    citation: str
    # "documented" -- the vendor or project names this detector in its own docs.
    # "absent"     -- searched and no such claim exists for this configuration.
    status: str


@dataclass(frozen=True)
class Needle:
    """One injectable value, with everything needed to defend publishing it."""

    id: str
    needle_class: str
    label: str
    # "fixed"  -- a literal, identical in every run and every seed.
    # "seeded" -- drawn reproducibly from the run seed.
    generation: str
    validity: str
    format_source: str
    format_source_quote: str
    non_live_basis: str
    detector_claims: tuple[DetectorClaim, ...]
    # Present only for fixed needles.
    value: str | None = None
    # Present only for seeded needles: the key `make_seeded_fixture` returns it under.
    seeded_key: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if len(self.id) > 10:
            # The v2 10-character entity-id cap. Vault retention is L = N - 1, so the
            # longest id is a hot-path cost in the reference implementation.
            raise ValueError(f"needle id {self.id!r} exceeds the 10-character cap")
        if self.needle_class not in NEEDLE_CLASSES:
            raise ValueError(f"unknown needle_class {self.needle_class!r}")
        if self.validity not in VALIDITY:
            raise ValueError(f"unknown validity {self.validity!r}")
        if self.generation == "fixed" and not self.value:
            raise ValueError(f"{self.id}: a fixed needle needs a value")
        if self.generation == "seeded" and not self.seeded_key:
            raise ValueError(f"{self.id}: a seeded needle needs a seeded_key")


# --------------------------------------------------------------------------------------
# The PII family. Values come from `make_seeded_fixture` rather than being restated, so
# the FIDE corpus and the v2 corpus cannot drift apart on the entity that both measure.
# --------------------------------------------------------------------------------------

_PRESIDIO = "Microsoft Presidio analyzer (image mcr.microsoft.com/presidio-analyzer)"

_PII_NEEDLES: tuple[Needle, ...] = (
    Needle(
        id="EMAIL",
        needle_class="pii",
        label="RFC 2606 reserved-domain email address",
        generation="seeded",
        seeded_key="EMAIL",
        validity="syntactically-valid",
        format_source="RFC 5322 s.3.4.1 addr-spec; RFC 2606 s.3 reserved TLDs",
        format_source_quote=(
            "RFC 2606 reserves example.com for documentation, so an address in it "
            "cannot be delivered to a real mailbox."
        ),
        non_live_basis=(
            "Local part drawn from the harness alphabet; domain is the IANA "
            "special-use documentation domain example.com."
        ),
        detector_claims=(
            DetectorClaim(_PRESIDIO, "image latest", "EMAIL_ADDRESS",
                          "presidio supported entities list", "documented"),
        ),
    ),
    Needle(
        id="SSN",
        needle_class="pii",
        label="US Social Security Number, unassigned 900-999 area",
        generation="seeded",
        seeded_key="SSN",
        validity="deliberately-invalid",
        format_source="SSA 'Social Security Number Randomization'; 20 CFR 422.103",
        format_source_quote=(
            "Area numbers 900-999 have never been assigned, so no living person's "
            "SSN can appear in this corpus."
        ),
        non_live_basis=(
            "Area 900-999 is never assigned. This is the fixture whose safety and "
            "detectability are in direct conflict: a validating detector (Google Cloud "
            "DLP) correctly refuses to flag it, which the profile publishes as "
            "detector_blind rather than engineering away."
        ),
        detector_claims=(
            DetectorClaim(_PRESIDIO, "image latest", "US_SSN",
                          "presidio supported entities list", "documented"),
        ),
    ),
    Needle(
        id="CARDPAN",
        needle_class="pii",
        label="Published test-range PAN",
        generation="seeded",
        seeded_key="CARDPAN",
        validity="syntactically-valid",
        format_source="ISO/IEC 7812-1; card-network published test PAN lists",
        format_source_quote="Luhn-valid PANs from the networks' own published test ranges.",
        non_live_basis="Drawn from the published test-card list in http_profile.py.",
        detector_claims=(
            DetectorClaim(_PRESIDIO, "image latest", "CREDIT_CARD",
                          "presidio supported entities list", "documented"),
        ),
    ),
    Needle(
        id="USPHONE",
        needle_class="pii",
        label="NANP 555-0100..0199 fictitious-use block",
        generation="seeded",
        seeded_key="USPHONE",
        validity="shape-valid",
        format_source="NANP 555 line-number assignment (ATIS/INC); see entity-list.md",
        format_source_quote=(
            "555-0100 through 555-0199 is widely cited as reserved for fictitious use. "
            "entity-list.md records that citation as UNVERIFIED: the ATIS/INC and NANPA "
            "primary sources were unreachable."
        ),
        non_live_basis=(
            "Line number restricted to 555-01xx. The reservation citation is UNVERIFIED "
            "and is labelled so here for the same reason it is labelled so in the "
            "entity registry."
        ),
        detector_claims=(
            DetectorClaim(_PRESIDIO, "image latest", "PHONE_NUMBER",
                          "presidio supported entities list", "documented"),
        ),
    ),
)


# --------------------------------------------------------------------------------------
# The secret family. All four are FIXED literals, not seeded draws. See
# `why_secrets_are_not_seeded` for the reasoning and its consequence for uncertainty.
# --------------------------------------------------------------------------------------

_DS = "Yelp detect-secrets"
_DS_VERSION = "1.5.0"

_SECRET_NEEDLES: tuple[Needle, ...] = (
    Needle(
        id="AWSKEYID",
        needle_class="secret",
        label="AWS access key ID, AWS's own published documentation example",
        generation="fixed",
        value="AKIAIOSFODNN7EXAMPLE",
        validity="syntactically-valid",
        format_source=(
            "AWS IAM User Guide, 'IAM identifiers' -> 'Understanding unique ID prefixes'; "
            "AWS CLI User Guide, 'Configuration and credential file settings'"
        ),
        format_source_quote=(
            "IAM identifiers, prefix table, retrieved 2026-09-09: 'AKIA  Access key'. "
            "AWS CLI credential-file example, retrieved 2026-09-09: "
            "'[default] aws_access_key_id=AKIAIOSFODNN7EXAMPLE'."
        ),
        non_live_basis=(
            "PUBLISHED BY AWS ITSELF as the example access key ID in its own credential "
            "file documentation, and it carries the literal token EXAMPLE. An access key "
            "ID is in any case only half a credential: it authenticates nothing without "
            "the paired secret access key, which is not present in this repository in "
            "any form. The value was never presented to any AWS endpoint."
        ),
        detector_claims=(
            DetectorClaim(_DS, _DS_VERSION, "AWSKeyDetector",
                          "detect_secrets/plugins/aws.py denylist "
                          r"(?:A3T[A-Z0-9]|ABIA|ACCA|AKIA|ASIA)[0-9A-Z]{16}",
                          "documented"),
            DetectorClaim("Google Cloud Sensitive Data Protection", "API v2",
                          "AWS_CREDENTIALS infoType",
                          "Cloud DLP infoType detector reference", "documented"),
            DetectorClaim(_PRESIDIO, "image latest", "-",
                          "Presidio's supported-entity list contains no credential "
                          "or secret entity type", "absent"),
        ),
        notes=(
            "Twenty characters: the AKIA prefix plus 16 uppercase alphanumerics, which "
            "is exactly the form the detect-secrets denylist encodes."
        ),
    ),
    Needle(
        id="GHTOKEN",
        needle_class="secret",
        label="GitHub personal access token shape, checksum region zeroed",
        generation="fixed",
        value="ghp_EXAMPLENOTAREALGITHUBTOKEN0000000000",
        validity="shape-valid",
        format_source=(
            "GitHub Engineering blog, 'Behind GitHub's new authentication token formats'"
        ),
        format_source_quote=(
            "Retrieved 2026-09-09: 'A 32 bit checksum in the last 6 digits of each "
            "token... We start the implementation with a CRC32 algorithm... We then "
            "encode the result with a Base62 implementation, using leading zeros for "
            "padding as needed.'"
        ),
        non_live_basis=(
            "COMPUTED, not asserted. The random portion of an issued token is uniform "
            "base62; this value's is a fixed English literal followed by ten consecutive "
            "zeros. The probability that GitHub's generator emits ten consecutive zeros "
            "in that position is 62**-10, about 1.2e-18. The value was never presented "
            "to any GitHub endpoint. NOTE THE LIMIT OF THE CLAIM: the blog post states "
            "that a CRC32 occupies the last six base62 characters but does not specify "
            "the exact input to the CRC, so this fixture is recorded as shape-valid "
            "rather than as a computed checksum failure. detect-secrets does not verify "
            "the checksum either -- its denylist is prefix plus length."
        ),
        detector_claims=(
            DetectorClaim(_DS, _DS_VERSION, "GitHubTokenDetector",
                          "detect_secrets/plugins/github_token.py denylist "
                          r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36}",
                          "documented"),
            DetectorClaim(_PRESIDIO, "image latest", "-",
                          "no credential entity type", "absent"),
        ),
        notes="Four-character prefix plus exactly 36 characters, per the detector pattern.",
    ),
    Needle(
        id="SLACKBOT",
        needle_class="secret",
        label="Slack bot token shape, all-zero numeric fields",
        generation="fixed",
        value="xoxb-00000-00000-EXAMPLENOTAREALTOKEN",
        validity="shape-valid",
        format_source="Slack API documentation, 'Token types'",
        format_source_quote=(
            "Retrieved 2026-09-09: 'Bot token strings begin with xoxb-.' The "
            "documentation states the prefix and does not specify field widths."
        ),
        non_live_basis=(
            "Both numeric fields are all zeros and the secret portion is a fixed "
            "literal containing EXAMPLE and NOTAREAL. The value was never presented to "
            "any Slack endpoint. UNVERIFIED, and labelled so: Slack does not publish a "
            "statement that zero is never assigned as a team or bot id, so the "
            "'not assignable' half of this argument rests on the fixed literal in the "
            "secret field rather than on the numeric fields."
        ),
        detector_claims=(
            DetectorClaim(_DS, _DS_VERSION, "SlackDetector",
                          "detect_secrets/plugins/slack.py denylist "
                          r"xox(?:a|b|p|o|s|r)-(?:\d+-)+[a-z0-9]+",
                          "documented"),
            DetectorClaim(_PRESIDIO, "image latest", "-",
                          "no credential entity type", "absent"),
        ),
        notes=(
            "THE NUMERIC FIELDS ARE DELIBERATELY SHORTER THAN AN ISSUED TOKEN'S. An "
            "issued bot token carries long decimal team and bot ids; a run of 13 or more "
            "digits also matches this corpus's own CARDPAN pattern, so a realistic "
            "Slack fixture would have been redacted by a PII recognizer and the secret "
            "result would have been carried by the wrong detector. Five-digit fields "
            "keep the value inside the documented prefix claim and inside the "
            "detect-secrets pattern while matching no PII detector in the corpus. This "
            "is a construct-validity choice and it narrows the fixture: it does not "
            "measure how a detector behaves on a full-length issued token."
        ),
    ),
    Needle(
        id="PEMBLOCK",
        needle_class="secret",
        label="PEM private-key encapsulation with a non-key payload",
        generation="fixed",
        value=(
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "Tk9UQVJFQUxLRVkK\n"
            "-----END RSA PRIVATE KEY-----"
        ),
        validity="deliberately-invalid",
        format_source="RFC 7468, 'Textual Encodings of PKIX, PKCS, and CMS Structures'",
        format_source_quote=(
            "RFC 7468 s.2, retrieved 2026-09-09: 'There is exactly one space character "
            "(SP) separating the BEGIN or END from the label. There are exactly five "
            "hyphen-minus... characters on both ends of the encapsulation boundaries.' "
            "Generators MUST put the same label on the END line as the BEGIN line."
        ),
        non_live_basis=(
            "COMPUTED. The base64 payload Tk9UQVJFQUxLRVkK decodes to the eleven ASCII "
            "bytes NOTAREALKEY followed by a newline. It is not DER, it is not an "
            "RSAPrivateKey structure, and no parser can extract key material from it "
            "because there is none. The encapsulation boundaries are valid RFC 7468 "
            "framing; the contents are English."
        ),
        detector_claims=(
            DetectorClaim(_DS, _DS_VERSION, "PrivateKeyDetector",
                          "detect_secrets/plugins/private_key.py denylist entry "
                          "'BEGIN RSA PRIVATE KEY'",
                          "documented"),
            DetectorClaim(_PRESIDIO, "image latest", "-",
                          "no credential entity type", "absent"),
        ),
        notes=(
            "THE DETECTABLE UNIT IS SHORTER THAN THE NEEDLE, and that is the point of "
            "including it. PrivateKeyDetector matches the 21-character header string, "
            "not the block: a partition that cuts inside the base64 body leaves the "
            "header intact and the detector still fires. Only a cut inside the header "
            "defeats it. The needle is therefore a case where FIDE is confined to a "
            "known sub-span, which is a different shape of result from the PII needles "
            "and must not be averaged with them without saying so.\n\n"
            "It is also the only needle containing whitespace and newlines, which is "
            "what makes it the test of the retention control's own bound: a retention "
            "rule that cuts at the last space is unsound for a unit that contains one."
        ),
    ),
)

NEEDLES: tuple[Needle, ...] = _PII_NEEDLES + _SECRET_NEEDLES

BY_ID: dict[str, Needle] = {n.id: n for n in NEEDLES}
BY_CLASS: dict[str, tuple[Needle, ...]] = {
    cls: tuple(n for n in NEEDLES if n.needle_class == cls) for cls in NEEDLE_CLASSES
}


def why_secrets_are_not_seeded() -> str:
    """The seed choice, and its consequence for the uncertainty analysis.

    The PII sweep uses twelve seeds because the seed changes the generated SSN, email,
    PAN and phone number, and a rate that moves with those values needs a distribution.
    The secret needles are FIXED literals for reasons that are not convenience:

      * every one of them is either a vendor's own published example or a
        deliberately-degenerate literal, and randomising it would destroy the
        non-liveness argument that makes it publishable at all;
      * a random AKIA-prefixed 20-character string is, at 32**16, almost certainly not
        anyone's key -- but "almost certainly" is not the standard this corpus uses for
        credentials, and AWS has already published a value that needs no such argument;
      * the detectors under test are regex denylists whose behaviour does not vary with
        the random portion, so a seed axis would add runtime and no information.

    THE CONSEQUENCE IS STATED RATHER THAN HIDDEN: for the secret family, seed-to-seed
    variation is zero BY CONSTRUCTION. There is no sampling distribution over seeds, so
    no seed-level confidence interval is identified and none is reported. The twelve
    seeds still vary the ECHO segment, so FidelityRate remains a twelve-seed quantity;
    the secret LeakRates and their DeltaFrag are single-valued and are published as
    point results with that reason attached.
    """
    return why_secrets_are_not_seeded.__doc__ or ""


def needle_values(seed: str) -> dict[str, str]:
    """Every needle's concrete value for one run, keyed by needle id.

    Seeded needles come from `make_seeded_fixture` so the FIDE corpus and the v2 corpus
    draw personal data from one generator. Fixed needles are literals.
    """
    rng = random.Random(seed)
    drawn = make_seeded_fixture(rng)
    out: dict[str, str] = {}
    for needle in NEEDLES:
        if needle.needle_class == "prompt_injection":
            continue
        if needle.generation == "seeded":
            out[needle.id] = drawn[needle.seeded_key or needle.id]
        else:
            assert needle.value is not None
            out[needle.id] = needle.value
    return out


def measurable_ids() -> tuple[str, ...]:
    """Needle ids with fixtures behind them. `prompt_injection` has none, on purpose."""
    return tuple(
        n.id for n in NEEDLES if n.needle_class != "prompt_injection"
    )


def classes_in_use() -> tuple[str, ...]:
    return tuple(sorted({BY_ID[i].needle_class for i in measurable_ids()}))


def class_of(needle_id: str) -> str:
    return BY_ID[needle_id].needle_class


def registry_digest() -> str:
    """Digest of the DEFINITIONS -- ids, classes, validity, values, claims.

    Fixed needle values are literals and are therefore part of what a reader must be able
    to pin. The PII values are drawn per seed and are not in this digest; the seed pins
    them, which is the same split `corpus.sha256` already makes between case definitions
    and drawn values.
    """
    payload = [
        {
            "id": n.id,
            "needle_class": n.needle_class,
            "generation": n.generation,
            "validity": n.validity,
            "value": n.value,
            "seeded_key": n.seeded_key,
            "format_source": n.format_source,
            "detector_claims": [
                [c.target, c.version, c.detector, c.status] for c in n.detector_claims
            ],
        }
        for n in NEEDLES
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def needle_scope_block(enabled: tuple[str, ...], source: str) -> dict[str, Any]:
    """The `entity_scope` block for a FIDE run, partitioned over the needle registry.

    Same contract as v2's: every id in the registry appears in exactly one of enabled,
    not_enabled or unknown, so a target that never had a class switched on cannot be
    recorded as having passed it.
    """
    measurable = set(measurable_ids())
    enabled_set = set(enabled) & measurable
    return {
        "mechanism": "needle-registry detector set",
        "enabled": sorted(enabled_set),
        "not_enabled": sorted(measurable - enabled_set),
        "unknown": [],
        "partitions_corpus": True,
        "recorded_by": "operator",
        "source": source,
    }


# --------------------------------------------------------------------------------------
# The safety test's machinery. Kept beside the fixtures rather than in the test file: a
# fixture added without a corresponding safety argument must fail here, not be forgotten
# somewhere else.
# --------------------------------------------------------------------------------------

# Shapes that mean "this looks like it could be a real credential". Used to scan the
# environment and the repository, NEVER to generate anything.
_CREDENTIAL_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key", re.compile(r"\b(?:A3T[A-Z0-9]|ABIA|ACCA|AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36}\b")),
    ("slack-token", re.compile(r"\bxox[abporsu]-[0-9A-Za-z-]{6,}\b")),
    ("pem-private-key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
)


def fixture_values() -> tuple[str, ...]:
    return tuple(n.value for n in NEEDLES if n.value)


def credential_shaped_strings(text: str) -> list[tuple[str, str]]:
    """Every credential-shaped run in `text`, as (shape, value)."""
    found: list[tuple[str, str]] = []
    for shape, pattern in _CREDENTIAL_SHAPES:
        for match in pattern.finditer(text):
            found.append((shape, match.group(0)))
    return found
