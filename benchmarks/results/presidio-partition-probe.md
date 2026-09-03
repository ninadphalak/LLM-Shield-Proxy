# Presidio partition probe — is a whole-string scanner chunk-composable?

**Run:** 2026-09-03, project-run, single machine. **Not independently reproduced.**

- Runner: `benchmarks/presidio_partition_probe.py` (committed; re-derivable on demand)
- Subject: `mcr.microsoft.com/presidio-analyzer`, stock recognizer registry, no ad-hoc
  recognizers, container up and healthy on `127.0.0.1:5002`
- Harness host: Windows 11, CPython 3.14, AMD64
- Fixture: the current valid, non-real benchmark fixture —
  `euefmius@example.com`, `939-38-8264`, `5555-5555-5555-4444`

## What this is, and what it is not

**It is** an existence check on a bug class: applying a whole-string PII scanner to
individual stream chunks does not protect values that straddle a chunk boundary.

**It is not** a comparison, a leaderboard row, a latency claim, or a defect report against
Presidio. Presidio does not claim to be a streaming scanner. Scanning per-chunk is the
integrator's decision. The property under test belongs to the *integration pattern*, and any
whole-string scanner used that way inherits it.

**Model of a chunk-local scanner:** each chunk is analysed independently with no state
retained between chunks. That is exactly the pattern bounded suffix retention replaces.

## Method

For each fixture value, place it in a carrier sentence, confirm whole-string analysis covers
the full value span, then split the stream at every internal offset of the value and analyse
the two chunks independently. Classify each split:

- **protected** — a chunk match covers the whole value; nothing leaks.
- **partial** — a chunk match covers only part of the value; the rest reaches the wire.
  Worse than a miss, because the output looks redacted.
- **missed** — no chunk matches; the whole value reaches the wire.

## Result

| Entity | Presidio type | Whole-string baseline | protected | partial | missed | split points |
| :--- | :--- | :--- | ---: | ---: | ---: | ---: |
| EMAIL | `EMAIL_ADDRESS` | covered | **0** | 8 | 11 | 19 |
| SSN | `US_SSN` | covered | **0** | 0 | 10 | 10 |
| CREDIT_CARD | `CREDIT_CARD` | covered | **0** | 0 | 18 | 18 |
| **Total** | | | **0** | **8** | **39** | **47** |

**Every value that whole-string analysis detects is detected. Not one of the 47 internal
split points protects it under chunk-local scanning.**

### The email case is the instructive one

`EMAIL` is the only entity with any chunk hits, and all 8 are partial:

- Splits 1–7 fall inside the local part. The right chunk still contains a *complete, valid
  but different* address — `euefmius@example.com` split at 5 leaves `ius@example.com`, which
  Presidio correctly detects. Redaction fires, and the leading characters of the local part
  go to the wire.
- Split 19 leaves `euefmius@example.co` on the left — a valid address with the `.co` TLD.
  Detected, and the trailing `m` goes to the wire.
- Splits 8–18 miss entirely.

So the fragment that survives is a value the scanner is *right* about and the integrator is
*wrong* about. This is the same defect class as the partial-redaction leak found in
LLM-Shield-Proxy's own Tier 1 (`319604c`), where `PHONE` consumed 8 of 12 Aadhaar digits and
left 4 on the wire — reproduced here in an unrelated implementation, from a different cause.

`SSN` and `CREDIT_CARD` show no partials because both recognizers validate: a fragment is not
a checksum-valid card or a well-formed SSN, so it produces nothing rather than something
wrong. **Validation converts partial leaks into clean misses.** It does not prevent the leak.

## Relationship to the LiteLLM row

`http-profile-litellm-1.99.0.md` row 2 measured LiteLLM's Presidio guardrail and recorded
that the client-visible stream arrives as **a single event**, because the `output_parse_pii`
path assembles the whole response before re-emitting it.

Read together, the two results describe the actual engineering trade-off: that integration
avoids this bug class by **giving up incremental delivery**. It buffers, so there are no
chunk boundaries to straddle. It also never restores the original values, so it is a one-way
anonymiser rather than a masking gateway.

That is the honest framing of what bounded suffix retention buys — not "we detect more", but
"the value can be protected without buffering the response."

## Limits

- One scanner, one recognizer registry, one language, three entity types, 47 split points.
- Two-part splits only. Multi-way fragmentation is not tested here.
- Project-run on one machine; no independent reproduction.
- The carrier sentence is fixed; no sensitivity analysis over carriers.
- Nothing here measures latency, throughput, or quality against any other product.
