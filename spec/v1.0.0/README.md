# Streaming Privacy Gateway Conformance Specification v1.0.0

Normative changes and result labeling follow the
[public governance process](../../website/docs/conformance/governance.md).

The normative human-readable specification is published in `website/docs/conformance/specification-v1.md` and rendered on the project documentation site.

Two machine-readable envelopes are published here. The specification keeps the Streaming
Privacy Gateway (SPG) name and the `llm-shield.` schema identifier, which is a format
namespace pinned into already-published raw artifacts from runs that cannot be re-executed;
the endpoint-neutral **tool** carries the neutral name instead, because a benchmark named
after one of the products it scores cannot referee them.



| File | Covers | Emitted by |
|---|---|---|
| `report.schema.json` | local implementation profile | `llm-shield-proxy benchmark` |
| `http-profile.schema.json` | OpenAI-compatible HTTP gateway profile | `pii-leak-benchmark` ([its own distribution](https://pypi.org/project/pii-leak-benchmark/)) |

Both reject a report whose top-level `passed` is `true` while any individual check failed, and
the egress/boundary checks reject a `passed` alongside leaked entities, an uninspected capture,
or a correlated-request count that no captured request could support. Neither can detect a
hand-edited measurement: `implementation.name`/`version` are operator-supplied labels, and any
`attestation` block is self-reported unless its `verification` names a mechanism a third party
can check without trusting the submitter.

Implementations may reuse the specification and schema under the repository's Apache License 2.0. Conformance claims must state the exact version and must not imply certification by the LLM-Shield-Proxy project.
