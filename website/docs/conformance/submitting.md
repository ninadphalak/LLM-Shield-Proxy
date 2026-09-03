# Submitting a Result

The [results table](./results) is open to independent verification. This page outlines how to submit a run, what the submission should contain, how it is validated, and the criteria for a result to be considered "replicated".

To submit a run, you must provide both the generated report and the exact configuration used. A result only achieves "replicated" status when three separate individuals submit runs for the exact same gateway and configuration.

## Why Replication is Counted (Not Averaged)

Currently, the results table relies primarily on initial project measurements. We encourage independent runs to validate these findings.

For each target, the table publishes:
- **Runs:** Total number of complete artifacts.
- **Distinct Submitters:** The number of unique accounts that provided the runs.
- **Versions Covered:** The exact target versions or image digests measured.
- **Date Range:** The timeline from the first to the most recent run.
- **Disagreements:** Published as separate rows, rather than averaged out.

Until three different people submit runs, the result is marked as `unreplicated`. This applies to all gateways, including LLM-Shield-Proxy. 

*Note: Runs performed by a gateway's maintainer do not count toward the required three independent replications.*

### Handling Disagreements

If two runs of the same target using the same pinned configuration yield different outcomes, both rows are kept. The target is marked as **disputed**, and the difference is documented. Disagreements are valuable because they often highlight undocumented defaults, version drift, or platform differences.

## What a Submission Should Contain

Include both of the following in a single pull request or issue:

1. **The Raw Artifact:** The unedited JSON output from the harness. Do not reformat, hand-correct, or partially quote it. If a field is incorrect, re-run the benchmark.
2. **The Pinned Configuration:** A Markdown record specifying:
   - The exact target version or image digest (e.g., `portkeyai/gateway@sha256:…`, `litellm[proxy]==1.99.0`).
   - The runtime environment (e.g., Python/Node version, OS).
   - **All settings affecting redaction:** Include verbatim details (guardrail IDs, plugins, config blocks). "Default configuration" is acceptable if true; a vague "PII redaction on" is insufficient.
   - Any features explicitly disabled that could impact the result.
   - The submitter's name and any affiliation with the vendor.

*Note: A run lacking configuration details cannot be reproduced and will not be published. Conversely, a configuration without an artifact is merely a claim, not a measurement.*

### Redact Before Submitting

The harness is designed to exclude tokens, API keys, and extra header values from the report. However, you should manually redact certain fields before publishing:

- `target.base_url`: May contain account identifiers for hosted gateways.
- `capture.target_must_be_preconfigured_for` and `capture.self_probe.advertised_url`: May expose your tunnel hostname or probe secret.

**Substitute these with a placeholder (e.g., `[REDACTED]`). Do not delete the field entirely,** as the schema requires it for validation.

## How a Run is Verified

Before a row is added, it must pass the following checks. Submissions failing any check will be sent back for correction:

| Check | Purpose |
| :--- | :--- |
| Schema Validation | Ensures the artifact validates against [`http-profile.schema.json`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/spec/v1.0.0/http-profile.schema.json). The `outcome` field is verified programmatically, preventing manual edits. |
| `capture.self_probe` | Confirms the capture server was reachable before routing target traffic, ensuring leak findings are valid. |
| `correlated_requests >= 1` | Verifies traffic reached the capture server. Zero correlation results in `inconclusive`, rather than a leak. |
| `marker_words_observed_max` | Distinguishes between aggressive redaction and misrouted traffic. |
| `leak_evidence[].match_kind` | Specifies whether findings were `literal` (verbatim) or `normalized`. Review this before confirming a leak. |
| `needle_proximity` | Validates the SSN match margins for the specific run. |
| `claim_citation` | Ensures the product's redaction claims are supported by documentation. |
| `capture.mode` | Identifies if the test ran locally (`loopback`) or over a public network (`public`). |
| No Credentials | Ensures no secrets are accidentally included in the JSON output. |

The `passed` raw measurement is never overwritten. The `outcome` field is strictly derived based on validation rules.

## How to Submit

1. Run the profile using the [Reproduction Guide](./reproducing) or the [Hosted Gateway Runbook](./hosted-gateway-runbook).
2. Open a Pull Request adding the artifact and configuration to `benchmarks/results/`, or attach them to a GitHub Issue.
3. State your affiliation. Vendor-submitted runs are welcome, count toward total runs, and will be labeled `vendor-submitted`.

We accept submissions for any OpenAI-compatible gateway, including unlisted platforms or runs that dispute existing rows.

## CI Automation

A run generated by your repository's CI system is easiest to verify because it provides detached provenance. We provide a composite GitHub Action at [`.github/actions/pii-leak-benchmark`](https://github.com/ninadphalak/LLM-Shield-Proxy/tree/main/.github/actions/pii-leak-benchmark) to automate this:

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

This action installs `pii-leak-benchmark`, runs the test, adds the outcome to the job summary, and uploads the report. GitHub signs a hash of the report, which you can verify using:

```bash
gh attestation verify pii-leak-benchmark-report.json -R submitter/repository
```

## Current Limitations

- **Replication Status:** Currently, all rows are `unreplicated`.
- **Identity Verification:** The GitHub signature proves which workflow created the report, but does not definitively prove the remote gateway process used the exact version stated. Peer review is still required.
- **Formatting Shims:** A gateway that purely formats data without true redaction may pass the fixture (see [Fixture Threat Model](./fixture-threat-model)).
