---
sidebar_position: 6
---

# The test fixture: one corrected bias and one known limitation

The protected fixture is the three values the HTTP profile plants in its prompt and then
looks for at the capture. Review found two distinct issues:

- **It was biased toward this project's own proxy.** All three values were *invalid
  specimens*, so a detector that validates its input correctly ignored them, while this
  project's non-validating regex engine caught them. **Fixed on 2026-09-01**, and the
  measurement that exposed it is preserved below.
- **It is gameable.** A target that matches the three formats and substitutes them
  passes without operating a detector on anything else. **Not fully fixed by design.**
  Values now vary per run, but the formats remain stable.

Every number on this page was produced by running the harness or by querying a pinned
Presidio analyzer. Nothing here is hypothetical.

## Defect 1: the fixture favored non-validating detection

This was the more serious issue because it affected normal validating detectors. No special
handling was required: a careful detector could reject the invalid specimens and appear worse
than a detector that matched their shapes without validating them.

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
two findings: `DATE_TIME` over the card number and `URL` over part of the email, with
nothing at all for the SSN.

### What that did to a real product

LiteLLM 1.99.0 with its documented Presidio PII-masking guardrail enabled reported
`leaked_entity_types: ["SSN"]` against the old fixture. The same gateway, same
configuration, same harness, with only the three constants replaced by detectable
equivalents, reported `leaked_entity_types: []`.

This project's own proxy passed both, because `engines/pii_engine.py` Tier 1 is bare
regex: `\d{3}-\d{2}-\d{4}` with no SSN range check and `(?:\d[ -]?){13,16}` with no Luhn.
For these values, the fixture gave the reference implementation an advantage unrelated to the
property being measured. That row was withheld rather than published, and the fixture was
replaced.

### The replacement

Every value must now satisfy two properties at once.

**VALID** - a detector that validates its input recognises it:

- Card: a Luhn-valid 16-digit PAN.
- SSN: non-zero group and serial, outside every prefix on Presidio's invalidation list,
  and outside every group range in its ITIN recognizer so it scores `US_SSN` and not
  `US_ITIN`.
- Email: a real public suffix.

**NON-REAL** - the value cannot identify anyone or route anywhere:

- `example.com`, reserved for documentation by RFC 2606 §3.
- The SSA has never issued a Social Security Number in the `900-999` area.
- Card numbers are **drawn from a published list of test PANs, never generated.**
  Generating a Luhn-valid number in an issued BIN could produce a live card, so the
  harness will not do it. The card therefore carries six possible values where the SSN
  carries about 3.1×10⁷. This deliberate asymmetry is published in every report as
  `fixture.value_space_nominal`.

One detail illustrates the validation problem: the
SSA publishes `987-65-4320` through `987-65-4329` for use in advertising, which is the obvious
"safe" SSN to reach for. Presidio scores it `US_ITIN` 0.5 and **no `US_SSN` at all**,
because its SSN recognizer blacklists the prefix `98765432`. The officially safe value is
precisely the one a careful detector ignores.

### A false-positive class removed at the same time

Round 7 recorded that exactly one valid IPv4 address, `123.45.67.89`, normalises to the
same digits as the old SSN fixture, so a tunnel adding an `x-forwarded-for` header could
produce an SSN finding against a gateway that had redacted correctly. That is no longer
disclosed and accepted. It is now **excluded by construction**: the generator resamples
any SSN whose digits some valid dotted quad could produce. A measured 37.3% of the
nominal SSN space is rejected on that rule alone.

### An additional decoder defect found during correction

The old SSN's digits, `123456789`, were a **substring** of the old card's digits,
`4532123456789012`. Every "the SSN was recovered" assertion in the evasion suite could
therefore be satisfied by decoding the *card*. That masked a real defect: the base64
decoder's alignment guard could not decode an 11-byte value out of a run carrying one
prefix character, so `x` + base64(SSN) was never recovered at all. Both are fixed, and a
test now asserts that no protected needle is a substring of another.

## Known limitation: fixed formats can be matched directly

A roughly 35-line `str.replace` shim with no detector produced a schema-valid report that passed
every check. Value variation has made that shim less specific: it now needs a working format
matcher rather than three string comparisons. The limitation remains because the formats must be
stable for results to be comparable.

### Why the fixture is not randomised further

Varying the **format** was measured and rejected. Of six plausible format variants, two
(space-separated and dot-separated SSNs) produced **false leak findings against this
project's own correctly redacting gateway**. A one-in-three false leak rate is worse than the
fixture weakness it would address.

Varying the **value** inside a fixed format is a different proposition and was never
tested in that earlier work. It has now been tested, and it costs nothing: values vary per
run, formats are byte-identical, and the live proxy passes five consecutive profiles with
`leaked: []` every time.

### The report-binding control

Bind the **artifact** to the run rather than the fixture to a random value: OIDC-signed
provenance over the report digest, produced by third-party CI under the submitter's own
account. The composite action implements this with `attest-report: true`; a reviewer checks the
downloaded JSON with `gh attestation verify`. Editing the JSON after the run then breaks
verification. This still does not authenticate the measured remote process, so every published
row requires the pinned package/image, configuration, public run, and raw artifact as well as a
passing JSON file.

## What this page does not claim

The fixed format also *helps* one measured property: it bounds what a target can put in
the body, which is what makes the cross-request fragment-reassembly margin measurable at
all. `needle_proximity` and `needle_lengths` publish that margin per run.

Neither defect is presented as fully solved. Defect 1 is fixed for the three entity types
the profile carries; it says nothing about detectors this project has not measured.
Defect 2 remains open and disclosed.
