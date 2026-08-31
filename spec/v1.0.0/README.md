# Streaming Privacy Gateway Conformance Specification v1.0.0

Normative changes and result labeling follow the
[public governance process](../../website/docs/conformance/governance.md).

The normative human-readable specification is published in `website/docs/conformance/specification-v1.md` and rendered on the project documentation site.

Two machine-readable envelopes are published here:

| File | Covers | Emitted by |
|---|---|---|
| `report.schema.json` | local implementation profile | `llm-shield-proxy benchmark` |
| `http-profile.schema.json` | OpenAI-compatible HTTP gateway profile | `llm-shield-proxy benchmark --target-base-url …` |

Both reject a report whose top-level `passed` is `true` while any individual check failed, and
the egress/boundary checks reject a `passed` alongside leaked entities, an uninspected capture,
or a correlated-request count that no captured request could support. Neither can detect a
hand-edited measurement: `implementation.name`/`version` are operator-supplied labels, and any
`attestation` block is self-reported unless its `verification` names a mechanism a third party
can check without trusting the submitter.

Implementations may reuse the specification and schema under the repository's Apache License 2.0. Conformance claims must state the exact version and must not imply certification by the LLM-Shield-Proxy project.
