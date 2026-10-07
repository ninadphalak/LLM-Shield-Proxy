---
title: Test a proxy from your own fork
sidebar_position: 4
---

You can measure a privacy proxy yourself, from a fork, and put the result on the
[results wall](./who-has-run-it.mdx). You need a GitHub account and about ten minutes of
clicking; the run itself takes 15 to 40 minutes. No model provider account, API key or
local install is needed. You fork the proxy you want to test, not this benchmark; the
exceptions are LLM-Shield-Proxy, where the proxy and the benchmark share a repository, and the
two libraries, LLM Guard and Guardrails AI, which have no proxy to fork and run from a fork of
this repository through the small gateway each one was measured through.

Running the workflow publishes nothing. Run it as often as you like, on any branch; a result
reaches the wall only when you choose to submit it.

Every published row that needs no account can be reproduced this way. The two cloud rows,
Google Cloud DLP and Model Armor, need a Google Cloud project and its credentials, so no fork
recipe exists for them.

## Pick a proxy

Choose one row of this table. The steps below send you back to it by column name.

| Proxy repository to fork | Workflow file to add | Workflow name in the Actions tab | What it measures |
| --- | --- | --- | --- |
| <span id="replicate-portkey"></span>[Portkey OSS Gateway](https://github.com/Portkey-AI/gateway) | [portkey-source-reproduction.yml](https://raw.githubusercontent.com/ninadphalak/LLM-Shield-Proxy/main/.github/workflows/portkey-source-reproduction.yml) | Reproduce Portkey source build | OSS output guardrail call-out with no authentication. |
| <span id="replicate-litellm"></span>[LiteLLM](https://github.com/BerriAI/litellm) | [litellm-source-reproduction.yml](https://raw.githubusercontent.com/ninadphalak/LLM-Shield-Proxy/main/.github/workflows/litellm-source-reproduction.yml) | Reproduce LiteLLM source with Presidio | Presidio `pre_call` guardrail with response restoration enabled. |
| <span id="replicate-nemo-guardrails"></span>[NeMo Guardrails](https://github.com/NVIDIA/NeMo-Guardrails) | [nemo-source-reproduction.yml](https://raw.githubusercontent.com/ninadphalak/LLM-Shield-Proxy/main/.github/workflows/nemo-source-reproduction.yml) | Reproduce NeMo Guardrails source with Presidio | Presidio-backed output detection. Refusals count as inconclusive, not clean. |
| <span id="replicate-llm-shield-proxy"></span>[LLM-Shield-Proxy](https://github.com/ninadphalak/LLM-Shield-Proxy) | None. It is already in the fork as [source-reproduction.yml](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/.github/workflows/source-reproduction.yml). | Reproduce proxy source build | Response redaction on, with a redaction-off arm as a control. |
| <span id="replicate-llm-guard"></span>LLM Guard 0.3.16, a library: fork [LLM-Shield-Proxy](https://github.com/ninadphalak/LLM-Shield-Proxy) | None. It is already in the fork as [wrappers-source-reproduction.yml](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/.github/workflows/wrappers-source-reproduction.yml). | Reproduce library wrappers | Its own scanners around each response, through [this gateway](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/llm-guard-v2-profile/gateway.py). Choose `llm-guard-chunk-local` (each chunk scanned alone) or `llm-guard-buffered` (the whole response scanned once). On a hosted runner about half the cases end without a complete response and are scored inconclusive, so the published LLM Guard rows are the workstation runs [below](#llm-guard-on-your-machine); the workflow still runs and reports what the runner saw. |
| <span id="replicate-guardrails-ai"></span>Guardrails AI 0.10.2, a library: fork [LLM-Shield-Proxy](https://github.com/ninadphalak/LLM-Shield-Proxy) | None. It is already in the fork as [wrappers-source-reproduction.yml](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/.github/workflows/wrappers-source-reproduction.yml). | Reproduce library wrappers | Its streaming validator, which holds text to the end of a sentence, with this project's four patterns inside it, through [this gateway](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/guardrails-v2-profile/gateway.py). Choose `guardrails-ai`. |

## Run it

Keep this page open in another tab; the steps send you back to the table above.

1. **Fork the proxy.** Click the link in the table's **Proxy repository to fork** column.
   On that GitHub page, click **Fork**, then **Create fork**. Everything you click from here
   on is in your fork, at `github.com/YOUR-NAME/...`, not in the original repository.
2. **Add the workflow file.** Skip this step for LLM-Shield-Proxy, LLM Guard and Guardrails
   AI: the file is already in your fork. For the other three proxies:
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
   - **LLM Guard, Guardrails AI:** there is one drop-down. Pick the wrapper from the table's
     last column, for example `llm-guard-buffered`. Leaving it on `all` runs every wrapper
     as its own job, but that run offers no submission link, because one issue is one row.

   Click the green **Run workflow** button at the bottom of the panel. The run appears in
   the list within a few seconds; refresh the page if it does not.
5. **Wait, then read the result.** A run takes 15 to 40 minutes. Click it in the list to
   open its summary page, then scroll down to the heading that starts with **Result:**. It
   says one of three things:

   | The summary says | What it means | Submit it? |
   | --- | --- | --- |
   | **Result: MEASURED LEAK** | The run worked and the proxy leaked. The job is red on purpose. | Yes. A leak is a result. |
   | **Result: MEASURED CLEAN** | The run worked and nothing leaked in any measured case. The job is green. | Yes. |
   | **Result: INCOMPLETE, do not submit** | Something failed before a measurement existed, so nothing was uploaded. It says nothing about the proxy. The summary says what went wrong and what to do next. | No. Follow the summary's **What to do**, then run again. |

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

## LLM Guard and Guardrails AI on your own machine {#llm-guard-and-guardrails-ai}

These two are libraries, not proxies. The results wall measured each one through a small gateway
in this repository that calls the library around every response. The table above runs that
gateway on a GitHub runner from a fork of this repository; this section runs the same gateway and
the same check on your own machine instead. You need Python 3.10, 3.11 or 3.12 for the library,
and Git. No model provider account or API key is needed.

The commands are for Linux and macOS. On Windows, use `py -3.12` for `python3.12`, and
`Scripts\` for `bin/` in every path.

1. **Get this repository and install the check.** The check goes in its own environment, apart
   from the library it measures:

   ```bash
   git clone https://github.com/ninadphalak/LLM-Shield-Proxy
   cd LLM-Shield-Proxy
   python3 -m venv venv-harness
   venv-harness/bin/pip install "./pii-leak-benchmark[validate]"
   ```

2. **Install the library and start its gateway**, in a second terminal in the same folder. Leave
   it running. Pick the one you are replicating.

3. **Run the check** from the first terminal, with the command under that library below. It
   takes a minute or two and writes one JSON report into `my-run/`. In the line it prints,
   `outcome=fail` means something leaked, and `schema=VALID` means the report is complete.

4. **Send it in.** A run on your own machine has no CI run to link, so it goes in as a report
   rather than through the one-click route above. Open an issue with the JSON report and the
   facts in [What a submission should contain](./submitting.md#what-a-submission-should-contain):
   the library version, your Python version and operating system, and the gateway mode you used.

### LLM Guard {#llm-guard-on-your-machine}

LLM Guard 0.3.16 installs PyTorch and transformer models, a download of several gigabytes. The
gateway has two modes, and the wall shows one configuration for each: `buffered` waits for the
whole response before scanning it, `chunk-local` scans each chunk alone. Start one:

```bash
python3.12 -m venv venv-llmguard
venv-llmguard/bin/pip install llm-guard==0.3.16
LLMGUARD_MODE=buffered venv-llmguard/bin/python benchmarks/llm-guard-v2-profile/gateway.py \
  --port 8790 --upstream http://127.0.0.1:8799/v1/chat/completions
```

Then run the check, naming the mode you started in `--only` (`llm-guard-buffered` or
`llm-guard-chunk-local`):

```bash
V2_REQUEST_PATH_REDACTION=configured venv-harness/bin/python -m pii_leak_benchmark.v2_emitter \
  --validate --only llm-guard-buffered --gateway-url http://127.0.0.1:8790/v1/chat/completions \
  --upstream-port 8799 --model capture --seed a1b2c3d4e5f60001 --out my-run
```

The mode is read once, when the gateway starts. To measure the other one, stop the gateway and
start it again with the other `LLMGUARD_MODE`. The gateway's own notes are in
[`benchmarks/llm-guard-v2-profile/gateway.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/llm-guard-v2-profile/gateway.py).

### Guardrails AI {#guardrails-ai-on-your-machine}

The gateway feeds each response through Guardrails AI 0.10.2's own streaming validator, which
holds text back until the end of a sentence, with this project's four detection patterns inside
it. Guardrails AI has no way to put a caller's own values back, so it is not asked to redact the
request, and the check is told so. A run should print the numbers on the wall: 2 of 16 values
leaked both whole and split (`leak_single=0.125 leak_adv=0.125`).

```bash
python3.12 -m venv venv-guardrails
venv-guardrails/bin/pip install guardrails-ai==0.10.2
venv-guardrails/bin/python benchmarks/guardrails-v2-profile/gateway.py --port 8791
```

Then run the check:

```bash
V2_REQUEST_PATH_REDACTION=not-configured venv-harness/bin/python -m pii_leak_benchmark.v2_emitter \
  --validate --only guardrails-ai-stream-validate \
  --gateway-url http://127.0.0.1:8791/v1/chat/completions \
  --upstream-port 8799 --model capture --seed a1b2c3d4e5f60001 --out my-run
```

The gateway's own notes, including why the caller gets none of their data back, are in
[`benchmarks/guardrails-v2-profile/gateway.py`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/benchmarks/guardrails-v2-profile/gateway.py).

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
