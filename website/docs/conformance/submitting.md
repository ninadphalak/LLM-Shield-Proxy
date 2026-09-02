# Submitting a result

The [results table](./results) is open to independent verification. This page describes
the submission path: what a run must carry, how it is checked, and what makes a row count
as replicated.

The short version: **pinned configuration and raw artifact, never one without the other**, and a
gateway does not read as a verdict until three runs from three distinct submitters exist.

## Why replication is counted, not averaged

Every measured row in the table today comes from the initial project-run measurement set rather
than an independent reproduction. Only runs by other operators can change that status.

So the table publishes, per target:

- **runs** — how many complete artifacts exist;
- **distinct submitters** — how many different accounts produced them;
- **versions covered** — the exact target versions/digests measured;
- **date range** — first and most recent run;
- **disagreements** — shown as separate rows, never averaged into one.

**Below 3 runs from 3 distinct submitters a target reads `unreplicated`, not a verdict.** That
floor applies to LLM-Shield-Proxy's own row, which is 1 run by 1 submitter and labelled the
reference implementation. It is not exempt from the same replication rule.

The maintainer's runs never count toward the replication of *any* row, including a competitor's.
Three runs by one operator are one setup measured three times.

### Disagreements are published as disagreements

If two runs of the same target and the same pinned configuration reach different outcomes, both
rows stay. The target is marked **disputed** and the difference is described. A disagreement is
usually a finding — an undocumented default, a version drift, a platform difference — and
averaging it away destroys exactly the information that made it worth publishing.

## What a submission must contain

Both of these, in one pull request or issue:

1. **The raw artifact.** The unedited JSON the harness wrote. Not reformatted, not hand-corrected,
   not partially quoted. If a field is wrong, re-run; do not edit.
2. **The pinned configuration**, as a Markdown record beside it, containing:
   - exact target version or image digest (`portkeyai/gateway@sha256:…`, `litellm[proxy]==1.99.0`);
   - the runtime it ran on (Python/Node version, OS);
   - **every setting that affects redaction**, verbatim — the guardrail, the plugin, the config
     block. "Default configuration" is an acceptable and useful answer; "PII redaction on" is not;
   - what was *not* enabled, when it plausibly would have changed the result;
   - who ran it and any affiliation with the vendor.

A run with an artifact and no configuration is unpublishable because it cannot be reproduced. A
configuration with no artifact is a claim, not a measurement.

### Redact before you submit

The token, API keys and extra-header values are never written to the report by design. These
fields can still carry things you do not want public:

- `target.base_url` — carries account identifiers for some hosted gateways;
- `capture.target_must_be_preconfigured_for` and `capture.self_probe.advertised_url` — carry your
  tunnel hostname and the per-run probe secret.

**Substitute a placeholder; do not delete the field** — the schema requires it, and a submission
that fails validation cannot be published.

## How a run is verified

Before a row appears, the following are checked. Anything that fails sends the submission back
rather than being published with a caveat.

| Check | What it proves |
| :--- | :--- |
| Validates against [`http-profile.schema.json`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/spec/v1.0.0/http-profile.schema.json) | The envelope is internally consistent. `outcome` is re-derived **in both directions**, so a hand-edited report fails here |
| `capture.self_probe` recorded and answered | The capture was reachable and recording *before* any target traffic, so a leak finding is not an artifact of a hijacked port |
| `configured_upstream_boundary.correlated_requests >= 1` | The captured traffic is attributable to this run. Zero correlation is `inconclusive`, never a leak |
| `marker_words_observed_max` | Distinguishes an over-redacting gateway from a target that sent its traffic elsewhere |
| `leak_evidence[].match_kind` | Whether each finding was `literal` (the value verbatim) or `normalized` (recovered only after joining and separator-stripping). **Read this before calling anything a leak** |
| `needle_proximity` / `needle_lengths` | The margin on this specific run. A validated loopback run measures SSN 2 of 9 |
| `redaction_claim.claim_citation` resolves and supports the claim | A product claim must be supported by a source before the benchmark assigns a pass or fail outcome |
| `capture.mode` | `loopback` is the stronger observation; `public` ran over a network the tester fronted |
| No credential appears anywhere in the artifact | The report gets pasted into issues |

`passed` is the raw measurement and is never overwritten. `outcome` is what the row may *say* —
see [the outcome table](./results#what-a-row-is-allowed-to-say). A submission cannot type its own
outcome.

## How to submit

1. Run the profile. [Reproduction guide](./reproducing) ·
   [hosted-gateway runbook](./hosted-gateway-runbook).
2. Open a pull request adding both files to `benchmarks/results/`, or open an issue with both
   attached if you would rather not open a PR.
3. State your affiliation. A run submitted by someone who works on the target is welcome and is
   labelled `vendor-submitted`; it counts as a run and as a distinct submitter, and the label
   stays on the row.

Submissions are accepted for any OpenAI-compatible gateway, including ones this project has never
heard of, including runs that differ from a published row.

## Running it in your own CI

A run that appears in your own repository's CI log, under your own account, is stronger evidence
than a file emailed to a maintainer. A run counts toward the independent-replication floor only
when the finished JSON bytes also carry verifiable, detached provenance from that CI run.

A composite GitHub Action ships in this repository at
[`.github/actions/pii-leak-benchmark`](https://github.com/ninadphalak/LLM-Shield-Proxy/tree/main/.github/actions/pii-leak-benchmark):

```yaml
permissions:
  contents: read
  id-token: write
  attestations: write

jobs:
  measure:
    runs-on: ubuntu-latest
    steps:
      - uses: ninadphalak/LLM-Shield-Proxy/.github/actions/pii-leak-benchmark@v1.3.5
        id: benchmark
        with:
          target-base-url: http://127.0.0.1:4000/v1
          target-name: your-gateway
          target-version: 1.2.3
          redaction-claimed: claimed
          redaction-claim-citation: https://your.docs/pii
          attest-report: "true"
```

It installs `pii-leak-benchmark` from PyPI, runs the profile, prints the outcome to the job
summary, uploads the artifact, and uses GitHub OIDC plus Sigstore to sign detached provenance over
the report digest. Verify the downloaded report against the repository that ran it:

```bash
gh attestation verify pii-leak-benchmark-report.json -R submitter/repository
```

The JSON's internal `attestation` block remains `self-reported`: embedding a digest or signature
inside the file being digested would be recursive. The proof is the detached GitHub attestation,
whose subject is the finished file's SHA-256 and whose signing identity names the submitter's
repository, workflow, commit and run. A hand-edited report no longer verifies against that proof.

## What is still missing

Recorded here rather than in a roadmap, because it bounds what the table can currently mean:

- **No independent reproduction exists yet.** Every row is `unreplicated`.
- **Target identity still needs scrutiny.** Signed report provenance proves which workflow emitted
  the JSON; it does not prove that the remotely measured process really was the version or image
  named by the submitter. The pinned configuration, package/image digest, public log and vendor
  review remain part of the evidence.
- **A format-matching shim can pass the fixture** - this is a known limitation explained
  in the [fixture threat model](./fixture-threat-model).
