---
sidebar_position: 6
---

# Test data: one fixed problem and one remaining limit

The HTTP test puts an email address, SSN, and card number in its prompt. It then checks whether the
gateway sends any of those values to the benchmark's capture server. Review found two problems:

- **The old values were invalid.** A detector that checks whether values are valid correctly ignored
  them, while a regex that only checked their shape matched them. This was fixed on 2026-09-01.
- **Code written for the three fixed formats can still pass.** Such code does not need to be a
  general PII detector. Values now change on every run, but their formats stay the same.

The measurements below come from the harness and a pinned Presidio analyzer.

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

The values were safe to publish, but they were not valid examples of the data types they claimed to
represent. The table shows the result from `mcr.microsoft.com/presidio-analyzer` with its stock
English recognizers and `score_threshold: 0.0`:

| Old fixture value | Presidio verdict | Why | A detectable control | Presidio verdict |
| :--- | :--- | :--- | :--- | :--- |
| `123-45-6789` | **nothing** | `UsSsnRecognizer.invalidate_result` blacklists the prefix `123456789` outright | `456-78-9012` | `US_SSN` 0.85 |
| `4532-1234-5678-9012` | **no `CREDIT_CARD`** | `CreditCardRecognizer.validate_result` runs Luhn; this sums to 68 | `4111-1111-1111-1111` | `CREDIT_CARD` 1.0 |
| `person@example.invalid` | `URL` 0.5, never `EMAIL_ADDRESS` | `EmailRecognizer.validate_result` requires a real public suffix via `tldextract`; `.invalid` has none | `bob@example.com` | `EMAIL_ADDRESS` 1.0 |

Run against the whole prompt rather than value by value, stock Presidio returned exactly
two findings: `DATE_TIME` over the card number and `URL` over part of the email, with
no finding for the SSN.

### How this affected a real product

LiteLLM 1.99.0 with its documented Presidio masking rule reported
`leaked_entity_types: ["SSN"]` with the old values. The same software and configuration reported
`leaked_entity_types: []` when only the three test values were changed to valid examples.

LLM-Shield-Proxy passed both runs because its Tier 1 detector used these regexes without an SSN
range check or Luhn check: `\d{3}-\d{2}-\d{4}` and `(?:\d[ -]?){13,16}`. The old test was easier for
LLM-Shield-Proxy than for a validating detector. The project did not publish the affected LiteLLM
result and replaced the test values.

### The replacement

Every new value must meet two rules.

**It must be valid:**

- Card: a Luhn-valid 16-digit PAN.
- SSN: non-zero group and serial, outside every prefix on Presidio's invalidation list,
  and outside every group range in its ITIN recognizer so it scores `US_SSN` and not
  `US_ITIN`.
- Email: a real public suffix.

**It must not belong to a real person or account:**

- `example.com`, reserved for documentation by RFC 2606 §3.
- The SSA has never issued a Social Security Number in the `900-999` area.
- Card numbers are **drawn from a published list of test PANs, never generated.**
  Generating a Luhn-valid number in an issued BIN could produce a live card, so the
  harness will not do it. The card therefore carries six possible values where the SSN
  carries about 3.1×10⁷. This deliberate asymmetry is published in every report as
  `fixture.value_space_nominal`.

The SSA publishes `987-65-4320` through `987-65-4329` for advertising examples. Presidio does
not classify those values as `US_SSN` because its SSN recognizer blocks the prefix `98765432`.
It instead scores them as `US_ITIN` 0.5. This makes the official example unsuitable for this
test.

### A false-positive class removed at the same time

The old SSN fixture had the same digits as the valid IPv4 address `123.45.67.89`. A tunnel could
therefore add an `x-forwarded-for` value that looked like a leaked SSN. The generator now rejects
any SSN whose digits can also form a valid IPv4 address. That rule rejects 37.3% of the nominal
SSN value space.

### An additional decoder defect found during correction

The old SSN's digits, `123456789`, were a **substring** of the old card's digits,
`4532123456789012`. Every "the SSN was recovered" assertion in the evasion suite could
therefore be satisfied by decoding the *card*. That masked a real defect: the base64
decoder's alignment guard could not decode an 11-byte value out of a run carrying one
prefix character, so `x` + base64(SSN) was never recovered at all. Both are fixed, and a
test now asserts that no protected needle is a substring of another.

## Known limitation: fixed formats can be matched directly

A small `str.replace` program written only for the three formats passed every check. Because values
now change on every run, such a program must at least match each format instead of matching three
fixed strings. It still does not need general PII detection.

### Why the fixture is not randomised further

Six format variations were tested. Two of them, space-separated and dot-separated SSNs, produced a
false leak result against LLM-Shield-Proxy even though it redacted the formats it documents. That
false failure rate was too high, so the benchmark keeps the three formats fixed.

Changing the value without changing its format did not cause that problem. Values now change on
every run, and LLM-Shield-Proxy passed five consecutive tests with no value found at the capture
server.

### The report-binding control

With `attest-report: true`, GitHub Actions signs a hash of the final JSON report. A reviewer can run
`gh attestation verify` to confirm which workflow created the file and whether it changed later.

This signature does not prove which remote service the gateway contacted or how that service was
configured. A published result must also include the exact package or image, configuration, run
details, and report.

## What this page does not claim

The fixed format also *helps* one measured property: it bounds what a target can put in
the body, which is what makes the cross-request fragment-reassembly margin measurable at
all. `needle_proximity` and `needle_lengths` publish that margin per run.

Neither defect is presented as fully solved. Defect 1 is fixed for the three entity types
the profile carries; it says nothing about detectors this project has not measured.
Defect 2 remains open and disclosed.
