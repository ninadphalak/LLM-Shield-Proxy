# Submitting a result

The [results table](./results) is open to independent verification. This page describes
the submission path: what a run must carry, how it is checked, and what makes a row count
as replicated.

Submit both the report produced by the run and the exact configuration used for the run. A result
becomes replicated only after three different people submit the same gateway and configuration.

## Why replication is counted, not averaged

Every measured row in the table today comes from the initial project-run measurement set rather
than an independent reproduction. Only runs by other operators can change that status.

So the table publishes, per target:

- **runs** - how many complete artifacts exist;
- **distinct submitters** - how many different accounts produced them;
- **versions covered** - the exact target versions/digests measured;
- **date range** - first and most recent run;
- **disagreements** - shown as separate rows, never averaged into one.

Until three different people submit runs, the result is `unreplicated`. This rule also applies to
LLM-Shield-Proxy, which currently has one run from this project's maintainer.

The maintainer's runs never count toward the replication of *any* row, including a competitor's.
Three runs by one operator are one setup measured three times.

### Disagreements are published as disagreements

If two runs of the same target and the same pinned configuration reach different outcomes, both
rows stay. The target is marked **disputed** and the difference is described. A disagreement is
usually a finding - an undocumented default, a version drift, a platform difference - and
averaging it away destroys exactly the information that made it worth publishing.

## What a submission must contain

Both of these, in one pull request or issue:

1. **The raw artifact.** The unedited JSON the harness wrote. Not reformatted, not hand-corrected,
   not partially quoted. If a field is wrong, re-run; do not edit.
2. **The pinned configuration**, as a Markdown record beside it, containing:
   - exact target version or image digest (`portkeyai/gateway@sha256:…`, `litellm[proxy]==1.99.0`);
   - the runtime it ran on (Python/Node version, OS);
   - **every setting that affects redaction**, verbatim - the guardrail, the plugin, the config
     block. "Default configuration" is an acceptable and useful answer; "PII redaction on" is not;
   - what was *not* enabled, when it plausibly would have changed the result;
   - who ran it and any affiliation with the vendor.

A run with an artifact and no configuration is unpublishable because it cannot be reproduced. A
configuration with no artifact is a claim, not a measurement.

### Redact before you submit

The token, API keys and extra-header values are never written to the report by design. These
fields can still carry things you do not want public:

- `target.base_url` - carries account identifiers for some hosted gateways;
- `capture.target_must_be_preconfigured_for` and `capture.self_probe.advertised_url` - carry your
  tunnel hostname and the per-run probe secret.

**Substitute a placeholder; do not delete the field** - the schema requires it, and a submission
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

`passed` is the raw measurement and is never overwritten. `outcome` is what the row may *say* -
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

A run from your own repository's CI is easier to verify than a file sent by email. A run counts toward the independent-replication floor only when the finished JSON report carries verifiable, detached provenance from that CI run.

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

The action installs `pii-leak-benchmark` from PyPI, runs the test, adds the outcome to the job
summary, and uploads the report. GitHub then signs a hash of that report. Verify the downloaded
file against the repository that ran it:

```bash
gh attestation verify pii-leak-benchmark-report.json -R submitter/repository
```

The report's own `attestation` block is self-reported. The separate GitHub attestation contains the
file's SHA-256 and identifies the repository, workflow, commit, and run. Editing the report after
the workflow makes verification fail.

## What is still missing

Recorded here rather than in a roadmap, because it bounds what the table can currently mean:

- **No independent reproduction exists yet.** Every row is `unreplicated`.
- **The signature does not prove the remote target's identity.** Signed report provenance proves which workflow created the report; it does not prove that the remotely measured process used the version or image named by the submitter. The exact configuration, package or image digest, public log, and vendor review are still needed.
- **A format-matching shim can pass the fixture** - this is a known limitation explained
  in the [fixture threat model](./fixture-threat-model).
