# What is in this directory, and what it is not

Every file here is a raw artifact from a run, plus the pinned configuration that produced
it — **never one without the other**. **None of it is an independent benchmark.** Every measured row was produced by this
project's maintainer against a target they installed themselves, on one Windows
workstation, and none has been independently reproduced. That is a conflict of interest,
and it is disclosed rather than hidden. Every target here therefore reads `unreplicated`
in the published table, which needs 3 runs from 3 distinct submitters before a row is a
verdict — this project's own row included. See
[governance](../../website/docs/conformance/governance.md) and
[submitting a result](../../website/docs/conformance/submitting.md).

Read `outcome` before `passed`. `passed` is the raw measurement — did all five checks
pass. `outcome` is what a table cell is permitted to say, derived from what the product
claims about PII redaction, what was configured, and only then what was measured. `fail`
means one thing and one thing only: protected data reached the capture origin.

## HTTP profile artifacts

| File | Target | `outcome` | Read it as |
| :--- | :--- | :--- | :--- |
| `http-profile-raw-capture-baseline.json` | Synthetic control, not a product | `fail` | The negative control. A raw pass-through with no redaction, standing in for a gateway that claims redaction and does not perform it. It must keep failing on three `literal` matches, or the harness has stopped being able to detect a leak. |
| `http-profile-llm-shield-proxy-working-tree.json` | This project | `pass` | A maintainer self-test of the reference implementation. One run, one submitter, unreplicated -- the row with the strongest incentive behind it and the weakest evidence under it. Configuration and known limits in `http-profile-llm-shield-proxy-working-tree.md`. |
| `http-profile-litellm-1.99.0-default.json` | LiteLLM 1.99.0, no guardrail | `redaction-not-enabled` | A statement about the configuration, **not a verdict about LiteLLM**. Nothing was enabled, so nothing was measured about its redaction. |
| `http-profile-litellm-1.99.0-presidio.json` | LiteLLM 1.99.0 + Presidio | `no-leak-profile-not-met` | **Not a leak finding.** Nothing protected reached the upstream. It does not restore the values to the client, which this profile requires. |
| `http-profile-portkey-gateway-oss-1.15.2-default.json` | Portkey Gateway OSS 1.15.2, no guardrails | `redaction-not-enabled` | As above: a configuration statement. |
| `http-profile-portkey-gateway-oss-1.15.2-regexreplace.json` | Portkey Gateway OSS 1.15.2 + `regexReplace` | `no-leak-profile-not-met` | **Not a leak finding.** Also note the patterns are tester-authored, so this measures Portkey's guardrail engine and not Portkey's detector. |

Configuration records: `http-profile-litellm-1.99.0.md`,
`http-profile-portkey-gateway-oss-1.15.2.md`,
`http-profile-llm-shield-proxy-working-tree.md`.

## Local profile artifact

`conformance-v1.0.0-pre-release-windows.json` is the local profile: this project's own
engines measured in process. It exercises no third party and is not comparable to
anything above.

## Latency

The `client_observed_latency` check in these artifacts **enforces no threshold** and gates
on sample completeness. Three iterations is a smoke test. Nothing in these files is a speed
comparison.

The configuration records used to carry a maintainer-local latency diagnostic: per-event
costs, a before/after speed multiplier for this proxy, and a head-to-head table against two
other gateways. **All of it has been withdrawn.** The runner and its raw samples were not
retained, so none of it could be re-derived on demand, and an independent re-measurement of
one component afterwards found a materially different magnitude. What the diagnostic
genuinely produced — two real defects in this project's own proxy, both now fixed and pinned
by `tests/test_streaming_write_efficiency.py` — is still recorded. The numbers are not.

No timing figure or speed multiplier is published anywhere in this repository. One would
need a versioned runner committed here, run end to end against every gateway compared, with
its raw output published beside it.

## Fixture

Reports carry `fixture` dimensions but never fixture values. The values are valid
specimens a validating detector recognises, drawn from reserved non-routable space, and
they vary per run with the format held byte-identical. An earlier fixture was biased
toward this project's own engine; that is recorded in
[the fixture threat model](../../website/docs/conformance/fixture-threat-model.md) rather
than quietly corrected.
