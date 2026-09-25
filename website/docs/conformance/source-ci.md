---
title: Reproduce a proxy source build in GitHub Actions
sidebar_position: 4
---

You can run the benchmark in a fork of the proxy you want to test. The workflow builds
the selected source commit, starts that gateway and a synthetic local capture, runs the
request-path check and the 32-case midpoint response check, and retains a verified
report bundle. No model provider account or API key is needed. You do not need to fork
the benchmark repository.

| Proxy repository to fork | Copy this workflow into that fork | Reference configuration |
| --- | --- | --- |
| [LLM-Shield-Proxy](https://github.com/ninadphalak/LLM-Shield-Proxy) | [source-reproduction.yml](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/.github/workflows/source-reproduction.yml) | Response redaction on and off; the manual default selects source tag `v1.6.6`. |
| [Portkey OSS Gateway](https://github.com/Portkey-AI/gateway) | [portkey-source-reproduction.yml](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/.github/workflows/portkey-source-reproduction.yml) | OSS output guardrail call-out with no authentication. |
| [LiteLLM](https://github.com/BerriAI/litellm) | [litellm-source-reproduction.yml](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/.github/workflows/litellm-source-reproduction.yml) | Presidio `pre_call` guardrail with response restoration enabled. |
| [NeMo Guardrails](https://github.com/NVIDIA/NeMo-Guardrails) | [nemo-source-reproduction.yml](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/.github/workflows/nemo-source-reproduction.yml) | Presidio-backed output detection; refusals remain inconclusive, not clean cases. |

1. Fork the proxy repository in the first column. Add the linked YAML file at the same
   `.github/workflows/` path in your fork, then commit it to your default branch. This
   is the only file you need to add to the proxy repository. The workflow installs or
   checks out the pinned benchmark itself.
2. In your fork, open **Actions**, select the new workflow, and click **Run workflow**.
   For Portkey, LiteLLM, or NeMo, leave `source_ref` blank to build the commit on the
   selected workflow branch. Enter a tag or commit if you want to measure a release
   source checkout. For LLM-Shield-Proxy, the manual default is `v1.6.6`; change it to
   the tag, branch, or commit you intend to test.
3. Open the completed run. Confirm that **Verify publishable evidence** succeeded and
   download the `source-reproduction` artifact. It contains the source commit,
   environment and dependency records, a request report, and a response report. The
   workflow file and run inputs identify the selected configuration. Raw specimen
   reports and post-fixture proxy logs are not uploaded.
4. To add the run to the [results wall](./who-has-run-it.mdx), follow the prefilled
   submission link in the job summary. Include the public Actions run URL and identify
   the product and configuration. The intake reads measurements from the run artifact,
   not from numbers typed into the submission.

A red job is not automatically a broken reproduction. The benchmark deliberately exits
nonzero for a measured privacy leak, while still retaining the verified evidence bundle.
If verification failed or the bundle is absent, the run is incomplete and should not be
submitted as a measurement. A refusal or inconclusive case is not a clean case.

The workflow records the selected source commit. Testing a tag checkout does not prove
that its bytes or dependencies equal a published wheel or container image. The result is
an operator-submitted source measurement, not tamper-resistant attestation against a
hostile fork or runner owner. Read the exact configuration and run URL before comparing
it with another row.

For another OpenAI-compatible proxy, use the [endpoint-neutral Action](./ci.mdx) and
add the build, startup, and local-capture routing appropriate to that proxy. The four
recipes above are concrete examples, not a universal build command.
