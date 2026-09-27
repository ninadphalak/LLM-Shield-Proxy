---
title: Test a proxy from your own fork
sidebar_position: 4
---

You can measure a privacy proxy yourself, from a fork, and put the result on the
[results wall](./who-has-run-it.mdx). You need a GitHub account and about ten minutes of
clicking; the run itself takes 15 to 40 minutes. No model provider account, API key or
local install is needed. You fork the proxy you want to test, not this benchmark; the one
exception is LLM-Shield-Proxy, where the proxy and the benchmark share a repository.

Running the workflow publishes nothing. Run it as often as you like, on any branch; a result
reaches the wall only when you choose to submit it.

## Pick a proxy

Choose one row of this table. The steps below send you back to it by column name.

| Proxy repository to fork | Workflow file to add | Workflow name in the Actions tab | What it measures |
| --- | --- | --- | --- |
| [Portkey OSS Gateway](https://github.com/Portkey-AI/gateway) | [portkey-source-reproduction.yml](https://raw.githubusercontent.com/ninadphalak/LLM-Shield-Proxy/main/.github/workflows/portkey-source-reproduction.yml) | Reproduce Portkey source build | OSS output guardrail call-out with no authentication. |
| [LiteLLM](https://github.com/BerriAI/litellm) | [litellm-source-reproduction.yml](https://raw.githubusercontent.com/ninadphalak/LLM-Shield-Proxy/main/.github/workflows/litellm-source-reproduction.yml) | Reproduce LiteLLM source with Presidio | Presidio `pre_call` guardrail with response restoration enabled. |
| [NeMo Guardrails](https://github.com/NVIDIA/NeMo-Guardrails) | [nemo-source-reproduction.yml](https://raw.githubusercontent.com/ninadphalak/LLM-Shield-Proxy/main/.github/workflows/nemo-source-reproduction.yml) | Reproduce NeMo Guardrails source with Presidio | Presidio-backed output detection. Refusals count as inconclusive, not clean. |
| [LLM-Shield-Proxy](https://github.com/ninadphalak/LLM-Shield-Proxy) | None. It is already in the fork as [source-reproduction.yml](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/.github/workflows/source-reproduction.yml). | Reproduce proxy source build | Response redaction on, with a redaction-off arm as a control. |

## Run it

Keep this page open in another tab; the steps send you back to the table above.

1. **Fork the proxy.** Click the link in the table's **Proxy repository to fork** column.
   On that GitHub page, click **Fork**, then **Create fork**. Everything you click from here
   on is in your fork, at `github.com/YOUR-NAME/...`, not in the original repository. If
   you forked it some time ago, delete that fork and fork again, so it has the current tags.
2. **Add the workflow file.** Skip this step for LLM-Shield-Proxy: the file is already in
   your fork. For the other three proxies:
   1. In the table's **Workflow file to add** column, click the file name that ends in
      `.yml`. A page of plain text opens. Select all of it and copy it (Ctrl+A, then
      Ctrl+C; on a Mac, Cmd+A, then Cmd+C).
   2. In your fork's **Code** tab, click **Add file**, then **Create new file**.
   3. In the file name box, type `.github/workflows/` followed by the file name you
      clicked, for example `.github/workflows/portkey-source-reproduction.yml`. Each `/`
      you type turns into a folder; that is expected.
   4. Paste into the large edit box. Click **Commit changes...**, leave it committing
      directly to the default branch, and click **Commit changes** again.
3. **Turn on Actions in your fork.** Open your fork's **Actions** tab and click
   **I understand my workflows, go ahead and enable them**. GitHub switches workflows off in
   every new fork until you do. Doing this after step 2 keeps the project's own workflows
   from starting, and failing, when you commit the file.
4. **Start the run.** In the **Actions** tab, the left column lists workflows by their name,
   not their file name, and a fork also lists the project's own workflows. Click the one in
   the table's **Workflow name in the Actions tab** column, for example
   **Reproduce Portkey source build**. If the list is long, click **Show more workflows...**
   at the bottom of it. Or go straight there by adding `/actions/workflows/` and the file
   name to your fork's address, for example
   `github.com/YOUR-NAME/litellm/actions/workflows/litellm-source-reproduction.yml`.

   A bar appears saying the workflow has a `workflow_dispatch` event trigger, with a
   **Run workflow** button on its right. Click it, and a small panel opens. Leave
   **Use workflow from** on the branch it shows. Then:
   - **Portkey, LiteLLM, NeMo Guardrails:** leave both text boxes empty to measure your
     fork's latest commit, or type a tag or commit into the first box to measure that
     instead.
   - **LLM-Shield-Proxy:** there is one box, already set to the release tag `v1.6.6`. Keep
     it, or change it to `main` to measure the latest code. Do not leave it empty.

   Click the green **Run workflow** button at the bottom of the panel. The run appears in
   the list within a few seconds; refresh the page if it does not.
5. **Wait, then read the result.** A run takes 15 to 40 minutes. Click it in the list to
   open its summary page, then scroll down to the heading that starts with **Result:**. It
   says one of three things:

   | The summary says | What it means | Submit it? |
   | --- | --- | --- |
   | **Result: MEASURED LEAK** | The run worked and the proxy leaked. The job is red on purpose. | Yes. A leak is a result. |
   | **Result: MEASURED CLEAN** | The run worked and nothing leaked in any measured case. The job is green. | Yes. |
   | **Result: INCOMPLETE, do not submit** | Something failed before a measurement existed, so nothing was uploaded. It says nothing about the proxy. | No. Rerun the workflow. |

## Put it on the wall

Below the **Result:** heading of a measured run, the summary offers two ways to submit.
Use either one:

- **One click.** Click the link **Submit this run to the results wall** at the bottom of the
  run's summary page. It opens a new issue on the benchmark repository,
  `ninadphalak/LLM-Shield-Proxy` (not your fork), with every field already filled in,
  including your run link. Type nothing; just click **Create** (some GitHub
  layouts label it **Submit new issue**).
- **One command**, from any terminal where the [GitHub CLI](https://cli.github.com/) is
  signed in. The summary prints it with your run's link filled in:

  ```
  gh issue create --repo ninadphalak/LLM-Shield-Proxy --title "Result: Portkey OSS Gateway" --body "https://github.com/YOUR-NAME/gateway/actions/runs/RUN-ID"
  ```

A bot answers on that issue within a few minutes. It either links your row on the
[results wall](./who-has-run-it.mdx), or says exactly what stopped it and what to do.
Editing the issue, for example to paste the link of a rerun, runs the check again.
When your row is published, that reply also gives you a README badge. It shows how many of
the three checks passed (the request path, a value whole in the response, and a value split
across two chunks), and 3 of 3 turns gold. Fix something, rerun, and edit the same issue
with the new run link: the badge in your README updates by itself.

The wall reads every number from your run's uploaded evidence, never from the issue. The
proxy's name, licence and tested configuration come from the workflow too, so the run link
is all the wall needs. The one exception: if you change the **Gateway** or
**Version and configuration** field in the issue before creating it, your text is shown
instead.

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
