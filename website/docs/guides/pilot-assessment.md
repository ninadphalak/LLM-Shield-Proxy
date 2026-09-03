# Privacy-Safe Pilot Assessment

The offline assessor converts representative JSON or JSONL traffic into an aggregate evaluation packet. This process does not call an upstream model and excludes source, transformed, or tokenized records from the final report.

## Run an Assessment

```bash
llm-shield-proxy assess \
  --input representative-traffic.jsonl \
  --out pilot-assessment \
  --assessment-plan-href urn:uuid:YOUR-ASSESSMENT-PLAN
```

Accepted inputs include a single JSON object, an array of objects/strings, or JSONL (one JSON object/string per line). Run this tool inside the organization that owns the data; it operates entirely offline and makes no network calls.

The output directory will contain:

- `assessment.json`: Contains the source SHA-256, configuration, record totals, and aggregate finding counts.
- `assessment.html`: A human-readable aggregate summary.
- `oscal-assessment-results.json`: OSCAL 1.2 Assessment Results metadata.

No matched values, prompts, transformed records, or reversible tokens are included in these files. The `source.bytes` value represents the sum of canonical JSON record sizes, not the physical size of the input file on disk.

Tier 2 (Entropy) is enabled by default unless `--disable-tier2` is supplied. Tier 3 (NER) is opt-in via `--enable-tier3` and uses only the locally configured ONNX model. The assessor does not download a model dynamically.

## Reproducibility

To ensure a repeatable aggregate JSON and deterministic OSCAL identifiers, pin the package version and configuration, retain the input file's SHA-256, and set a stable timestamp:

```bash
SOURCE_DATE_EPOCH=1767225600 llm-shield-proxy assess --input traffic.jsonl --out assessment
```

*Note: The default OSCAL assessment-plan URN is a placeholder. Update it with a real identifier before treating the artifact as formal evidence.*

## Suggested Pilot Acceptance Criteria

Before running the sample, organizations should establish acceptance criteria. Consider agreeing on:
- In-scope protected entity types.
- Acceptable false-positive and false-negative review processes.
- Expected behavior for streaming fragmentation.
- Fail-closed behavior and audit-delivery modes.
- Production-like load profiles.

The resulting report supplies evidence for these criteria; it does not certify compliance or replace human control testing.
