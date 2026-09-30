# Benchmark Actions

This repository keeps two existing composite Actions for different compatibility contracts.

## Operator CI and regression Action

The [root Action](../../action.yml) is the supported operator CI and regression surface. It can
start a managed gateway, run the operator profile against current and baseline targets, write the
operator and raw reports, render the result badge, and upload one report artifact.

The root Action does not create a detached attestation.

## Legacy research HTTP-profile Action

The [nested Action](./pii-leak-benchmark/action.yml) is the legacy research HTTP profile and
detached-attestation compatibility surface. Existing research workflows may continue to use it to
produce the schema-valid raw report and, when explicitly enabled with the required permissions, a
detached GitHub attestation.

Its inputs and outputs remain supported for compatibility.
