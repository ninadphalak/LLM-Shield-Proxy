---
title: Test a proxy from your own fork
sidebar_position: 4
---

You can measure a privacy proxy yourself, from a fork, and put the result on the
[results wall](./who-has-run-it.mdx). You need a GitHub account and about ten minutes of
clicking; the run itself takes 15 to 40 minutes. No model provider account, API key or
local install is needed, and you do not fork this benchmark repository.

## Pick a proxy

| Proxy repository to fork | Workflow file to add | What it measures |
| --- | --- | --- |
| [Portkey OSS Gateway](https://github.com/Portkey-AI/gateway) | [portkey-source-reproduction.yml](https://raw.githubusercontent.com/ninadphalak/LLM-Shield-Proxy/main/.github/workflows/portkey-source-reproduction.yml) | OSS output guardrail call-out with no authentication. |
| [LiteLLM](https://github.com/BerriAI/litellm) | [litellm-source-reproduction.yml](https://raw.githubusercontent.com/ninadphalak/LLM-Shield-Proxy/main/.github/workflows/litellm-source-reproduction.yml) | Presidio `pre_call` guardrail with response restoration enabled. |
| [NeMo Guardrails](https://github.com/NVIDIA/NeMo-Guardrails) | [nemo-source-reproduction.yml](https://raw.githubusercontent.com/ninadphalak/LLM-Shield-Proxy/main/.github/workflows/nemo-source-reproduction.yml) | Presidio-backed output detection. Refusals count as inconclusive, not clean. |
| [LLM-Shield-Proxy](https://github.com/ninadphalak/LLM-Shield-Proxy) | Already in the fork: [source-reproduction.yml](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/.github/workflows/source-reproduction.yml) | Response redaction on, with a redaction-off arm as a control. |

## Run it

1. **Fork** the proxy repository from the first column (the **Fork** button on its GitHub
   page, then **Create fork**).
2. **Turn on Actions in your fork.** Open your fork's **Actions** tab. GitHub disables
   workflows in a new fork until you click **I understand my workflows, go ahead and
   enable them**.
3. **Add the workflow.** Open the file link from the second column and copy all of it. In
   your fork, click **Add file**, then **Create new file**. Name it
   `.github/workflows/` followed by the same file name, for example
   `.github/workflows/portkey-source-reproduction.yml`. Paste, then **Commit changes**
   to the default branch. Skip this step for LLM-Shield-Proxy: the file is already there.
4. **Run it.** In **Actions**, pick the workflow in the left column and click
   **Run workflow**. Leave every box empty to measure your fork's latest commit, or type a
   tag or commit to measure that instead. For LLM-Shield-Proxy the box starts at `v1.6.6`;
   change it to `main` for the latest code.
5. **Read the result.** Open the finished run and scroll to the bottom of its summary.
   The last section says one of three things:

   | The summary says | What it means | Submit it? |
   | --- | --- | --- |
   | **MEASURED LEAK** | The run worked and the proxy leaked. The job is red on purpose. | Yes. A leak is a result. |
   | **MEASURED CLEAN** | The run worked and nothing leaked in any measured case. The job is green. | Yes. |
   | **INCOMPLETE, do not submit** | Something failed before a measurement existed, so nothing was uploaded. It says nothing about the proxy. | No. Rerun the workflow. |

## Put it on the wall

The summary of a measured run ends with two ways to submit. Use either one:

- **One click.** Click **Submit this run to the results wall**. It opens an issue on this
  repository with every field already filled in. Press **Create**.
- **One command**, from any terminal where the [GitHub CLI](https://cli.github.com/) is
  signed in. The summary prints it with your run's link filled in:

  ```
  gh issue create --repo ninadphalak/LLM-Shield-Proxy --title "Result: Portkey OSS Gateway" --body "https://github.com/YOUR-NAME/gateway/actions/runs/RUN-ID"
  ```

A bot answers on that issue within a few minutes. It either links your row on the
[results wall](./who-has-run-it.mdx), or says exactly what stopped it and what to do.
Editing the issue, for example to paste the link of a rerun, runs the check again.

The wall reads every number from your run's uploaded evidence, not from anything typed in
the issue. The workflow already names the proxy, its licence and the configuration it
tested, so the run link is all the wall needs. If you type your own gateway name or
version into the issue, what you typed is used instead.

## What the result does and does not show

The workflow builds the commit it records, starts that proxy on the runner, and sends it
synthetic personal data through a local capture that stands in for the model provider.
It checks two things: whether raw values reach the provider (the request path), and
whether values injected into a streamed response reach the client, whole or split across
two chunks (the response path).

A row names the source commit it was built from. Testing a tag's source does not prove
that its bytes or dependencies equal a published package or container image. A row is an
operator-submitted measurement, not tamper-resistant attestation: whoever controls a fork
controls what its runner does. Read the configuration and the run link before comparing
two rows.

To measure a different OpenAI-compatible proxy, start from the
[endpoint-neutral Action](./ci.mdx) and add that proxy's build and startup steps. The four
workflows above are worked examples of exactly that.
