---
sidebar_position: 6
---

# The fixture is gameable: threat model and decision

A benchmark that a vendor can pass without building the thing being measured is worth
nothing. This page states that weakness in the current HTTP profile plainly, measures both
sides of the obvious fix, and records the decision. Nothing here is hypothetical: every
number below was produced by running the harness.

## The weakness

The HTTP profile's prompt contains three hard-coded constants:

```text
person@example.invalid
123-45-6789
4532-1234-5678-9012
```

They are the same in every run, they are public in this repository, and they are the only
values the boundary check looks for. So a target does not need a detector. It needs three
string replacements and their inverses.

**Measured.** A shim of roughly 35 lines — `str.replace` on the way out, `str.replace` on
the way back, no detection of any kind — was run against the profile:

```text
passed: True
  configured_upstream_boundary: True
  fragmentation_safety:         True
  sse_validity:                 True
  response_fidelity:            True
  client_observed_latency:      True
leaked: []   correlated: 3
```

The resulting report validates against `spec/v1.0.0/http-profile.schema.json`. A vendor who
wants a passing row can produce one, honestly formatted and schema-valid, without operating
a gateway. This is the referee-play threat model: the harness's value is that its verdicts
mean something, and this is the cheapest way to make one mean nothing.

## The obvious fix, and what it costs

Vary the fixture per run — generate a fresh email, SSN and card each time — so there is no
constant to hard-code against.

That defeats the shim. It also fails honest gateways, because a detector is tuned to the
formats its author anticipated, and "PII this detector does not recognise" is not the same
finding as "gateway that leaks".

**Measured, against this project's own reference proxy** — the same gateway that passes the
current fixed fixture cleanly on five consecutive runs. Only the fixture format was varied;
nothing else changed:

| Fixture variant | Result |
|---|---|
| Baseline (current fixed fixture) | passed, `leaked: []` |
| SSN `123 45 6789`, card `4532 1234 5678 9012` (space-separated) | **`leaked: ["SSN"]`, failed** |
| SSN `123456789`, card `4532123456789012` (no separators) | passed, `leaked: []` |
| Amex `3782-822463-10005`, `person+tag@example.invalid` | passed, `leaked: []` |
| SSN `123.45.6789`, Mastercard, subdomain email | **`leaked: ["SSN"]`, failed** |
| Discover card, `contact@example.technology` | passed, `leaked: []` |

**Two of six variants — a third of them — turn an honest gateway into a published leak
finding.** Both failures are the same root cause: a space- or dot-separated SSN that the
detector's regex does not match. The gateway did nothing wrong. It has a detector whose SSN
pattern expects hyphens, which is an ordinary and defensible engineering choice.

A one-in-three false-accusation rate is not a tuning problem to be fixed with a better
generator. It is inherent: any generated value is a bet that every honest detector
recognises that format, and the space of plausible formats is larger than any single
detector's coverage. The failure is also silent in the worst way — `leaked_entity_types:
["SSN"]` is the harness's strongest claim, the one field that asserts protected data
reached the upstream, and it would be firing on a gateway that redacted everything it was
built to redact.

This is the rule six rounds have taught, in a new place: **fixes that raise a limit get
beaten, fixes that enumerate a channel hold.** Randomising the fixture raises a limit. It
makes the constant harder to guess without making the *channel* — "the target knows what
we will send" — any narrower.

## Would a fixed public fixture plus a varying private one work?

This is the version worth taking seriously, and it is how several credible benchmarks
handle contamination. Publish the fixed fixture so anyone can reproduce a run; keep a
second, unpublished fixture that the referee uses when it runs the target itself.

It works, but only under conditions this project does not currently meet:

- **It requires the referee to run the target.** A private fixture in a harness the vendor
  executes is not private — it is in the process memory of the software under test, and
  recovering it is trivial. So the private half only has force in runs the referee performs
  on infrastructure it controls. That is a different product from "a benchmark you run in
  your own CI", which is the current §7.3 item 3 goal.
- **It inherits the same false-positive rate.** A private fixture is still a chosen format.
  If the referee's private SSN is space-separated, an honest gateway fails, and now it
  fails against a fixture it cannot inspect to contest the finding. Private fixtures make
  false accusations *less* falsifiable, not more.
- **It splits the results into two incomparable columns**, and the public column is still
  gameable, so the public column's rows would carry no more weight than they do today.

So: workable in principle, wrong sequencing for this project now. It is a thing to build
once there is a reason to run targets on referee-controlled infrastructure, not a fix to
apply to the current self-service harness.

## Recommendation

**Do not vary the fixture. Change what the artifact claims instead.**

The fixture is not the load-bearing part of the design, and treating it as one is the
error. What makes a conformance result meaningful is not that the input was unguessable —
it is that the *artifact is bound to a run the reader can trace*. A report is currently a
JSON file anybody can type, with an `attestation` block of environment strings; a random
fixture would not change that, because the vendor produces both the shim and the report.

Three changes, in priority order, each of which does more than a varying fixture would:

1. **Bind the artifact to the run.** OIDC-signed provenance over the report digest, from
   third-party CI under the submitter's own account. A submitter can still run a shim, but
   they must do it in a public log under their own name, against a commit anyone can read.
   That converts fraud from "edit three constants" into "publish a fake gateway", which is
   a reputational act rather than a technical one. This is §7.3 item 3 and it is the real
   fix.
2. **State the limit in the report and in the results table.** The profile measures the
   behaviour of the endpoint it was pointed at during the run. It does not establish that
   the endpoint is the product it is labelled as, and `implementation.name` is already
   marked `labels_are_operator_supplied: true`. The fixture's fixedness belongs next to
   that statement, as a `method_limits` entry, not as an unstated assumption.
3. **Require the configuration and image digest with any published row**, which is already
   policy for the comparison table. A row is a pinned configuration plus a raw artifact
   plus a target identity — a passing JSON file alone was never sufficient.

None of that is a code change to the fixture, which is why the fixture stays as it is.

## What this does not claim

The fixed fixture also *helps* one measured property, and removing it would cost that too:
it bounds what a target can put in the body, which is what makes the cross-request
fragment-reassembly margin measurable. On an honest three-iteration run the concatenated
per-channel haystacks carry about 54 digits in total, and the longest run matching the
nine-digit normalised SSN needle is two characters. A per-run random fixture would make
that margin a function of the generator rather than a fixed, checkable property.

The gameability described here is real and unmitigated today. It is recorded rather than
quietly fixed because the available fix is worse than the defect, and because a referee
that hides its own weaknesses has already stopped being one.
