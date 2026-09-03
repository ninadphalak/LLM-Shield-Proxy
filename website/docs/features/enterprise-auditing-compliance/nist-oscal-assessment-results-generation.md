# NIST OSCAL 1.2 Assessment Results Generation

[⬅️ Back to Features Catalog](/docs/features-overview)

## What It Does
The proxy generates privacy-safe, machine-readable OSCAL 1.2 `assessment-results` artifacts from runtime governance decisions and offline pilot assessments. This standardizes evidence transfer to GRC tools, but it is not a formal auditor attestation.

## How It Works
Artifacts contain metadata about proxy observations (e.g., decision type, hashes, counts) but strictly omit the actual prompt text, detected PII values, or sensitive redaction tokens.

* **Runtime Exporter:** The `DecisionTraceExporter` retains privacy-safe decision metadata in memory and serializes the observations into OSCAL format when requested.
* **Offline Assessment:** The CLI command `llm-shield-proxy assess` generates OSCAL files without persisting source records, using deterministic UUIDs derived from the input fingerprint so assessments are reproducible.

## OSCAL 1.2 Compatibility Notice (Breaking Changes)
* `metadata.oscal-version` is now `1.2.0`. Ensure your ingestion pipelines and validators are updated.
* `DecisionTraceExporter` now generates fresh UUIDs on every call to prevent collision in GRC stores. Do not rely on previous hardcoded UUID constants for correlation; use hashes and timestamps instead.

## Performance Profile
- **Overhead:** Generating OSCAL artifacts is relatively fast but serializing large collections of observations requires memory and CPU. Run offline assessments outside the critical proxy path.

## Implementation Details & Edge Cases
* **Assessment Plan Placeholder:** The generated OSCAL artifacts use a placeholder `import-ap` value. You must replace this with your actual assessment-plan URN before treating the artifact as formal audit evidence.
* **Delivery:** Delivery, retention, and downstream validation remain your responsibility. The proxy simply generates the artifact.

## FAQ

**Q: Do these artifacts contain PII?**
A: No. They are designed to be privacy-safe and only contain structural metadata, hashes, and control outcomes.

## Practical Effect
This feature provides a standardized, machine-readable format for security observations, making it easier to integrate proxy decisions into automated GRC pipelines and auditor reviews.

## Related Tests
Tests: 
- `tests/test_tool_rbac_and_compliance.py`
- `tests/test_assessment.py`
- `tests/test_compliance_report.py`
