"""What a published row is ALLOWED to say about a product. Standard library only.

This module exists because the harness can measure a product it has no business
judging. Cloudflare AI Gateway is caching, rate limiting, observability and routing.
It does not claim to redact PII. Running a privacy benchmark against it and printing
"Fail" measures it against something it never offered -- that is a smear, it is not
retractable, and it ends this project's claim to be a neutral referee. The same trap
was already identified for Presidio's ``replace``/``hash``/``mask`` operators, which
leak nothing and cannot rehydrate.

So a measurement is not a verdict. A verdict requires three things to line up:

1. the product CLAIMS PII redaction, with a citation a reader can check;
2. that redaction was CONFIGURED for this run;
3. the run was attributable at all.

Miss any one and the run still produced honest measurements -- they are published
unchanged -- but the OUTCOME is not "Fail".

**The outcome is DERIVED here, never typed by whoever ran the harness.** The operator
supplies only the inputs (what the vendor claims, with a citation, and what was turned
on). If the outcome were a free string, a vendor facing a real failure would write
``not-applicable`` and the whole scheme would be worth nothing. The published schema
re-derives the same rule, so a hand-edited report fails validation.

Fail-closed default: an unstated claim yields ``claim-unstated``, which is not
publishable as a verdict in either direction. You may not say Pass or Fail about a
product until you have written down what it claims and cited where it says so.
"""

from __future__ import annotations

from typing import Any, Optional

# What the vendor says about PII redaction, as recorded by the operator.
CLAIM_CLAIMED = "claimed"
CLAIM_NOT_OFFERED = "not-offered"
CLAIM_UNKNOWN = "unknown"
CLAIM_VALUES = (CLAIM_CLAIMED, CLAIM_NOT_OFFERED, CLAIM_UNKNOWN)

# The publishable classification of the run. `passed` remains the raw measurement --
# did all five checks pass -- and is never overwritten. These say what a table cell is
# permitted to read.
OUTCOME_PASS = "pass"
OUTCOME_FAIL = "fail"
OUTCOME_NOT_APPLICABLE = "not-applicable"
OUTCOME_NOT_ENABLED = "redaction-not-enabled"
OUTCOME_NO_LEAK_PROFILE_NOT_MET = "no-leak-profile-not-met"
OUTCOME_INCONCLUSIVE = "inconclusive"
OUTCOME_CLAIM_UNSTATED = "claim-unstated"
OUTCOME_VALUES = (
    OUTCOME_PASS,
    OUTCOME_FAIL,
    OUTCOME_NO_LEAK_PROFILE_NOT_MET,
    OUTCOME_NOT_APPLICABLE,
    OUTCOME_NOT_ENABLED,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_CLAIM_UNSTATED,
)

# Verdicts about the product's measured behaviour. `no-leak-profile-not-met` is one
# too -- it is a real finding about reversible masking -- but it is emphatically NOT
# a leak finding, and a table must not collapse it into one.
VERDICT_OUTCOMES = (OUTCOME_PASS, OUTCOME_FAIL, OUTCOME_NO_LEAK_PROFILE_NOT_MET)
# The only outcome that asserts protected data reached the capture origin.
LEAK_OUTCOMES = (OUTCOME_FAIL,)

_RATIONALE = {
    OUTCOME_PASS: (
        "The product claims PII redaction, redaction was configured for this run, and "
        "every check passed. This is a verdict."
    ),
    OUTCOME_FAIL: (
        "Protected fixture data reached the configured capture origin. This is a leak "
        "finding and a verdict. Read checks.configured_upstream_boundary.leak_evidence "
        "before describing it: a 'literal' match is the value verbatim, a 'normalized' "
        "match was recovered only after joining and stripping separators."
    ),
    OUTCOME_NO_LEAK_PROFILE_NOT_MET: (
        "NOT A LEAK. Nothing protected reached the capture origin, but at least one "
        "behavioural check did not pass -- most commonly a one-way anonymizer that "
        "replaces values with identifiers and never restores them, so the client cannot "
        "receive the original value and response_fidelity fails (fragmentation_safety "
        "gates on the same reconstruction and fails with it). This profile requires "
        "REVERSIBLE masking; a product that deliberately does not rehydrate is not "
        "leaking, it is doing something else. Publish it as 'no leak; does not meet the "
        "reversible-masking requirement', never as a privacy failure."
    ),
    OUTCOME_NOT_APPLICABLE: (
        "The product does not claim to offer PII redaction, so this profile measures it "
        "against something it never offered. The measurements below are real, but they "
        "are NOT a verdict about this product and must never be published as a failure. "
        "Report it as 'Not applicable - no redaction feature offered'."
    ),
    OUTCOME_NOT_ENABLED: (
        "The product offers PII redaction but it was not configured for this run. This "
        "is a statement about the configuration, not a verdict about the product. Enable "
        "the feature and re-run before publishing any Pass or Fail."
    ),
    OUTCOME_INCONCLUSIVE: (
        "The run could not be attributed to the target: no request carrying this run's "
        "marker reached the capture. Never configured, unable to reach the capture, and "
        "sent elsewhere are indistinguishable here, so this is not a verdict. Confirm "
        "the target's configured upstream and re-run."
    ),
    OUTCOME_CLAIM_UNSTATED: (
        "No PII-redaction claim was recorded for this target, so the run cannot be "
        "classified. Record what the vendor claims, with a citation, and whether "
        "redaction was enabled. Until then this artifact is not publishable as a row."
    ),
}


def normalize_claim(claim: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Validate and normalize the operator-supplied claim block.

    Raises ValueError rather than guessing. A wrong default here is exactly the smear
    this module exists to prevent, so there is no permissive path.
    """
    if claim is None:
        # Fail closed: nothing was stated, so nothing may be published.
        return {
            "vendor_claims_pii_redaction": CLAIM_UNKNOWN,
            "configured_for_this_run": False,
            "recorded_by": "operator",
        }
    if not isinstance(claim, dict):
        raise ValueError("redaction_claim must be a dict")

    unknown_keys = set(claim) - {
        "vendor_claims_pii_redaction",
        "claim_citation",
        "claim_quote",
        "configured_for_this_run",
        "configuration_reference",
    }
    if unknown_keys:
        raise ValueError(f"redaction_claim has unknown keys: {sorted(unknown_keys)}")

    claimed = claim.get("vendor_claims_pii_redaction", CLAIM_UNKNOWN)
    if claimed not in CLAIM_VALUES:
        raise ValueError(
            f"vendor_claims_pii_redaction must be one of {list(CLAIM_VALUES)}, "
            f"got {claimed!r}"
        )

    citation = (claim.get("claim_citation") or "").strip()
    if claimed != CLAIM_UNKNOWN and not citation:
        # A claim without a citation is an assertion by whoever ran the harness about
        # somebody else's product. That is the thing a referee may not do.
        raise ValueError(
            "claim_citation is required whenever vendor_claims_pii_redaction is "
            f"{CLAIM_CLAIMED!r} or {CLAIM_NOT_OFFERED!r}. Cite where the vendor states "
            "it (a documentation URL), so a reader can check the claim rather than "
            "trust the submitter."
        )

    configured = bool(claim.get("configured_for_this_run", False))
    reference = (claim.get("configuration_reference") or "").strip()
    if claimed == CLAIM_CLAIMED and configured and not reference:
        raise ValueError(
            "configuration_reference is required when redaction was configured for the "
            "run. Name the exact setting, guardrail, or config file that turned it on, "
            "so the row can be reproduced."
        )
    if claimed != CLAIM_CLAIMED and configured:
        raise ValueError(
            "configured_for_this_run cannot be true unless "
            f"vendor_claims_pii_redaction is {CLAIM_CLAIMED!r}."
        )

    block: dict[str, Any] = {
        "vendor_claims_pii_redaction": claimed,
        "configured_for_this_run": configured,
        # Operator-supplied, like implementation.name. Not measured by the harness.
        "recorded_by": "operator",
    }
    if citation:
        block["claim_citation"] = citation
    quote = (claim.get("claim_quote") or "").strip()
    if quote:
        block["claim_quote"] = quote
    if reference:
        block["configuration_reference"] = reference
    return block


def derive_outcome(
    claim: dict[str, Any],
    *,
    passed: bool,
    attributable: bool,
    leaked: bool,
) -> str:
    """Classify the run. Precedence is deliberate and must not be reordered.

    The claim is checked BEFORE the measurement, because whether a verdict is meaningful
    at all is prior to what the verdict would be. A product that never offered redaction
    does not get a Fail no matter how the checks came out.

    ``leaked`` is the narrow question -- did protected data reach the capture origin --
    and it is what separates a leak finding from every other kind of non-pass.
    """
    claimed = claim.get("vendor_claims_pii_redaction", CLAIM_UNKNOWN)
    if claimed == CLAIM_UNKNOWN:
        return OUTCOME_CLAIM_UNSTATED
    if claimed == CLAIM_NOT_OFFERED:
        return OUTCOME_NOT_APPLICABLE
    if not claim.get("configured_for_this_run", False):
        return OUTCOME_NOT_ENABLED
    if not attributable:
        return OUTCOME_INCONCLUSIVE
    if passed:
        return OUTCOME_PASS
    return OUTCOME_FAIL if leaked else OUTCOME_NO_LEAK_PROFILE_NOT_MET


def rationale_for(outcome: str) -> str:
    return _RATIONALE[outcome]
