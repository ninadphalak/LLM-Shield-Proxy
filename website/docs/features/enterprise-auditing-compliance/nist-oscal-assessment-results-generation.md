# NIST OSCAL 1.2 Assessment Results

[Back to Features Catalog](/docs/features-overview)

## What it does

LLM-Shield-Proxy creates privacy-safe OSCAL 1.2 `assessment-results` artifacts from runtime governance decisions and offline pilot assessments. OSCAL is a machine-readable exchange format. The artifact supports control assessment and evidence transfer; it is not a compliance determination or auditor attestation.

Artifacts contain observation descriptions and selected metadata such as decision type, record hash, Merkle root, or aggregate entity count. They do not contain prompt text, detected values, or reversible redaction tokens.

## OSCAL 1.2 compatibility notice

The next release changes two externally visible artifact behaviors:

- `metadata.oscal-version` moves from `1.1.2` to `1.2.0`. Treat this as an artifact schema-version bump and update validators or transformations before upgrading.
- `DecisionTraceExporter.generate_oscal_artifact()` now creates fresh document and result UUIDs on every call. Previous versions reused hardcoded UUID constants, which could make unrelated artifacts collide in a GRC store.

Consumers must not compare, cache, join, or deduplicate runtime artifacts using the previous constant UUID values. Use explicit evidence metadata, request identifiers, hashes, and timestamps for correlation.

Offline assessment artifacts are intentionally different: they can derive deterministic document, result, and observation UUIDs from the input fingerprint and detector configuration so a frozen assessment can be reproduced byte-for-byte when its timestamp is also fixed.

## Shared builder and maintenance boundary

`llm_shield_proxy/compliance/oscal.py` is the single shared OSCAL builder. Both `assessment.py` and `compliance/trace_exporter.py` call it. Future OSCAL shape or version changes belong in that module instead of being duplicated in each caller.

The builder's default `import-ap` value is a placeholder assessment-plan URN. Replace it with the deployment's actual assessment plan before treating an artifact as formal audit evidence.

## Runtime exporter

`DecisionTraceExporter` retains privacy-safe decision metadata in process memory and can serialize the current observations:

```python
from llm_shield_proxy.compliance.trace_exporter import DecisionTraceExporter

exporter = DecisionTraceExporter()
artifact = exporter.generate_oscal_artifact()
```

Configured GRC transports can receive individual OSCAL decision deltas asynchronously. Delivery, retention, receiving-system validation, and correlation remain deployment responsibilities.

## Offline assessment

The assessment CLI writes aggregate JSON, HTML, and OSCAL files without persisting source or transformed records:

```bash
llm-shield-proxy assess representative.jsonl --out assessment-output
```

See the [pilot assessment guide](/docs/guides/pilot-assessment) for the input contract and reproduction controls.

## Consumer upgrade checklist

- Accept OSCAL `1.2.0` in validators and ingestion pipelines.
- Stop depending on the old runtime document or result UUID constants.
- Regression-test OSCAL-to-GRC mappings.
- Supply the real assessment-plan reference.
- Validate that receiving systems preserve hashes, timestamps, and evidence properties.
- Configure retention and checkpoint anchoring separately when long-lived audit evidence is required.

## Related tests

- `tests/test_tool_rbac_and_compliance.py`
- `tests/test_assessment.py`
- `tests/test_compliance_report.py`
