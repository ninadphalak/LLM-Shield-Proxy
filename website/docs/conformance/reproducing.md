# Reproduce the Conformance Report

## From a source checkout

```bash
python -m pip install -e .
llm-shield-proxy benchmark \
  --iterations 10000 \
  --json-out CONFORMANCE_LATEST.json
```

The runner performs no public-model call and writes no test PII into the report. Set the exact revision explicitly when running outside GitHub Actions:

```bash
LLM_SHIELD_SOURCE_REVISION=$(git rev-parse HEAD) \
  llm-shield-proxy benchmark --iterations 10000 --json-out CONFORMANCE_LATEST.json
```

On PowerShell:

```powershell
$env:LLM_SHIELD_SOURCE_REVISION = git rev-parse HEAD
py -m llm_shield_proxy.cli benchmark --iterations 10000 --json-out CONFORMANCE_LATEST.json
```

## Verify the artifact

Confirm that:

1. `schema` ends in `/v1.0.0`;
2. `source_revision` equals the revision tested;
3. all seven `checks` are present and pass;
4. protected vector values are absent;
5. timing scope excludes components not exercised;
6. memory scope distinguishes Python allocations from process RSS.

Use `benchmarks/REPORTING.md` for a production-shaped comparison. Publish unsuccessful runs and deviations alongside successful results.

## Contribute an independent reproduction

Open a GitHub Discussion or pull request containing the unmodified JSON artifact, host/runtime description, command, and a statement of affiliation or conflict of interest. Independent artifacts will be listed separately from project-maintainer results.
