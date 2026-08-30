# Privacy-Safe Pilot Assessment

The offline assessor turns representative JSON or JSONL traffic into an aggregate evaluation packet without calling an upstream model and without writing source, transformed, or tokenized records to the report.

## Run an assessment

```bash
llm-shield-proxy assess \
  --input representative-traffic.jsonl \
  --out pilot-assessment \
  --assessment-plan-href urn:uuid:YOUR-ASSESSMENT-PLAN
```

Accepted input is a JSON object, an array of objects or strings, or one JSON object/string per JSONL line. Run it inside the organization that owns the sample; the command performs no network calls.

The output directory contains:

- `assessment.json`: source SHA-256, configuration, record totals, and aggregate finding counts
- `assessment.html`: human-readable aggregate summary
- `oscal-assessment-results.json`: OSCAL 1.2 Assessment Results metadata

No matched value, prompt, transformed record, or reversible token is included. The `source.bytes` value is the sum of canonical JSON record sizes, not the input file's physical size.

Tier 2 is enabled unless `--disable-tier2` is supplied. Tier 3 is opt-in with `--enable-tier3` and uses only the locally configured ONNX model; the assessor never downloads a model.

## Reproducibility

Pin the package version and configuration, retain the input SHA-256, and set a stable timestamp:

```bash
SOURCE_DATE_EPOCH=1767225600 llm-shield-proxy assess --input traffic.jsonl --out assessment
```

For identical input, configuration, software, and environment, `SOURCE_DATE_EPOCH` makes the aggregate JSON and deterministic OSCAL identifiers repeatable. The default OSCAL assessment-plan URN is only a placeholder; replace it before treating the artifact as formal evidence.

## Suggested pilot acceptance criteria

Agree on criteria before running the sample: protected entity types in scope, acceptable false-positive/false-negative review process, streaming fragmentation cases, fail-closed behavior, audit-delivery mode, and a production-like load profile. The report supplies evidence; it does not certify compliance or replace human control testing.
