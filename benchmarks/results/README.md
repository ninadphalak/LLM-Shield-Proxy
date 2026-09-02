# What is in this directory, and what it is not

Each result in this directory has two parts: the report produced by the run and the exact
configuration used for that run. All current results were produced by this project on one Windows
workstation. No outside contributor has repeated them.

A result becomes `replicated` only after three different people each submit a run of the same
gateway and configuration. Until then, the table marks it `unreplicated`. See
[governance](../../website/docs/conformance/governance.md) and
[submitting a result](../../website/docs/conformance/submitting.md).

Read `outcome` before `passed`. `passed` records whether all five checks passed. `outcome` also
accounts for the product's documented PII-redaction claim and the configuration that was tested.
`fail` means the gateway sent an unmasked test value to the benchmark's capture server.

## HTTP profile artifacts

| File | Target | `outcome` | Read it as |
| :--- | :--- | :--- | :--- |
| `http-profile-raw-capture-baseline.json` | Synthetic control, not a product | `fail` | The negative control. A raw pass-through with no redaction, standing in for a gateway that claims redaction and does not perform it. It must keep failing on three `literal` matches, or the harness has stopped being able to detect a leak. |
| `http-profile-llm-shield-proxy-working-tree.json` | LLM-Shield-Proxy | `pass` | One run by this project's maintainer, not yet independently repeated. Configuration and known limits are in `http-profile-llm-shield-proxy-working-tree.md`. |
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

An earlier local diagnostic included timing numbers, but the runner and raw samples were not saved.
Those numbers were removed because they cannot be reproduced. The diagnostic still found two real
LLM-Shield-Proxy bugs. Both are fixed and covered by
`tests/test_streaming_write_efficiency.py`.

This directory does not compare gateway speed. A future comparison would need one saved runner,
the same end-to-end method for every gateway, and the raw output from every run.

## Fixture

Reports carry `fixture` dimensions but never fixture values. The values are valid
specimens a validating detector recognises, drawn from reserved non-routable space, and
they vary per run with the format held byte-identical. An earlier fixture favored
LLM-Shield-Proxy's pattern-based detector. That problem is recorded in
[the fixture threat model](../../website/docs/conformance/fixture-threat-model.md) rather
than quietly corrected.
