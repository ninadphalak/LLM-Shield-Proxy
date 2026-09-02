---
sidebar_position: 6
---

# The fixture: two defects, one fixed and one accepted

The protected fixture is the three values the HTTP profile plants in its prompt and then
looks for at the capture. Two separate things have been wrong with it, and they point in
opposite directions.

- **It was biased toward this project's own proxy.** All three values were *invalid
  specimens*, so a detector that validates its input correctly ignored them, while this
  project's non-validating regex engine caught them. **Fixed on 2026-09-01**, and the
  measurement that exposed it is preserved below.
- **It is gameable.** A target that matches the three formats and substitutes them
  passes without operating a detector on anything else. **Not fixed**, deliberately, and
  value variation has since raised the price of that shim without removing it.

Every number on this page was produced by running the harness or by querying a pinned
Presidio analyzer. Nothing here is hypothetical.

## Defect 1: the fixture measured carelessness as a virtue

**This is the more serious of the two, and it is the one nobody had to cheat to exploit.**
The gameable-fixture problem below needs a vendor to commit deliberate fraud. This one
needed nobody: any engineer at a competing gateway could have found it in ten minutes and
reasonably concluded the fixture had been chosen to flatter its author.

The profile used to ship these three constants:

```text
person@example.invalid
123-45-6789
4532-1234-5678-9012
```

Each was chosen to be safe to publish. Each is therefore exactly what a validating
detector is built to reject. Measured against `mcr.microsoft.com/presidio-analyzer`,
stock recognizer registry, `score_threshold: 0.0`, English:

| Old fixture value | Presidio verdict | Why | A detectable control | Presidio verdict |
| :--- | :--- | :--- | :--- | :--- |
| `123-45-6789` | **nothing** | `UsSsnRecognizer.invalidate_result` blacklists the prefix `123456789` outright | `456-78-9012` | `US_SSN` 0.85 |
| `4532-1234-5678-9012` | **no `CREDIT_CARD`** | `CreditCardRecognizer.validate_result` runs Luhn; this sums to 68 | `4111-1111-1111-1111` | `CREDIT_CARD` 1.0 |
| `person@example.invalid` | `URL` 0.5, never `EMAIL_ADDRESS` | `EmailRecognizer.validate_result` requires a real public suffix via `tldextract`; `.invalid` has none | `bob@example.com` | `EMAIL_ADDRESS` 1.0 |

Run against the whole prompt rather than value by value, stock Presidio returned exactly
two findings — `DATE_TIME` over the card number and `URL` over part of the email — and
nothing at all for the SSN.

### What that did to a real product

LiteLLM 1.99.0 with its documented Presidio PII-masking guardrail enabled reported
`leaked_entity_types: ["SSN"]` against the old fixture. The same gateway, same
configuration, same harness, with only the three constants replaced by detectable
equivalents, reported `leaked_entity_types: []`.

This project's own proxy passed both, because `engines/pii_engine.py` Tier 1 is bare
regex: `\d{3}-\d{2}-\d{4}` with no SSN range check and `(?:\d[ -]?){13,16}` with no Luhn.
**The fixture rewarded the reference implementation for being less careful than the
competitor it was scoring.** That row was withheld rather than published, and the fixture
was replaced instead.

### The replacement

Every value must now satisfy two properties at once.

**VALID** — a detector that validates its input recognises it:

- Card: a Luhn-valid 16-digit PAN.
- SSN: non-zero group and serial, outside every prefix on Presidio's invalidation list,
  and outside every group range in its ITIN recognizer so it scores `US_SSN` and not
  `US_ITIN`.
- Email: a real public suffix.

**NON-REAL** — the value cannot identify anyone or route anywhere:

- `example.com`, reserved for documentation by RFC 2606 §3.
- The SSA has never issued a Social Security Number in the `900-999` area.
- Card numbers are **drawn from a published list of test PANs, never generated.**
  Generating a Luhn-valid number in an issued BIN could produce a live card, so the
  harness will not do it. The card therefore carries six possible values where the SSN
  carries about 3.1×10⁷ — a deliberate asymmetry, published in every report as
  `fixture.value_space_nominal`.

One measured detail is worth keeping, because it is the whole problem in miniature: the
SSA publishes `987-65-4320`–`987-65-4329` for use in advertising, which is the obvious
"safe" SSN to reach for. Presidio scores it `US_ITIN` 0.5 and **no `US_SSN` at all**,
because its SSN recognizer blacklists the prefix `98765432`. The officially safe value is
precisely the one a careful detector ignores.

### A false-positive class removed at the same time

Round 7 recorded that exactly one valid IPv4 address, `123.45.67.89`, normalises to the
same digits as the old SSN fixture, so a tunnel adding an `x-forwarded-for` header could
produce an SSN finding against a gateway that had redacted correctly. That is no longer
disclosed and lived with — it is **excluded by construction**: the generator resamples
any SSN whose digits some valid dotted quad could produce. A measured 37.3% of the
nominal SSN space is rejected on that rule alone.

### And a fail-open defect the old fixture had been hiding

The old SSN's digits, `123456789`, were a **substring** of the old card's digits,
`4532123456789012`. Every "the SSN was recovered" assertion in the evasion suite could
therefore be satisfied by decoding the *card*. That masked a real defect: the base64
decoder's alignment guard could not decode an 11-byte value out of a run carrying one
prefix character, so `x` + base64(SSN) was never recovered at all. Both are fixed, and a
test now asserts that no protected needle is a substring of another.

## Defect 2: the fixture is gameable, and stays that way

A ~35-line `str.replace` shim with no detector produced a schema-valid report passing
every check. Value variation has since raised the price — the cheapest passing shim is now
a working format matcher rather than three string comparisons — but it has not removed the
weakness, and it never can: the formats must be stable for the profile to mean anything.

### Why the fixture is not randomised further

Varying the **format** was measured and rejected. Of six plausible format variants, two
(space-separated and dot-separated SSNs) produced **false leak findings against this
project's own correctly-redacting gateway**. A one-in-three false-accusation rate on the
harness's strongest claim is worse than the defect it would address.

Varying the **value** inside a fixed format is a different proposition and was never
tested in that earlier work. It has now been tested, and it costs nothing: values vary per
run, formats are byte-identical, and the live proxy passes five consecutive profiles with
`leaked: []` every time.

### The real fix, still outstanding

Bind the **artifact** to the run rather than the fixture to a random value: OIDC-signed
provenance over the report digest, produced by third-party CI under the submitter's own
account. That converts fraud from "edit three constants" into "publish a fake gateway
under your own name", which is a reputational act rather than a technical one. Until then
every report carries the limitation in `limitations.method_limits`, and a published row
requires a pinned configuration and a raw artifact as well as a passing JSON file.

## What this page does not claim

The fixed format also *helps* one measured property: it bounds what a target can put in
the body, which is what makes the cross-request fragment-reassembly margin measurable at
all. `needle_proximity` and `needle_lengths` publish that margin per run.

Neither defect is presented as fully solved. Defect 1 is fixed for the three entity types
the profile carries; it says nothing about detectors this project has not measured.
Defect 2 is open and disclosed. A referee that hides its own weaknesses has already
stopped being one.
