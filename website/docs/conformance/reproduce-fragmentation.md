---
sidebar_position: 4
title: Reproduce the fragmentation result
---

# Reproduce the fragmentation result

One bounded experiment. It compares a chunk-local inspector against a length-bounded
retaining inspector on the same corpus, and re-derives the published numbers on your
machine.

It runs offline on a laptop. No gateway, no cloud account, no API key, no model, no
network egress. Two policies, about 35 seconds each.

You do not need to read the paper, run any other gateway, or know anything about SOC 2 or
HIPAA to do this.

## Steps

### 1. Get the code

```bash
git clone https://github.com/ninadphalak/LLM-Shield-Proxy.git
cd LLM-Shield-Proxy
```

If you were given a specific commit, check it out now:

```bash
git checkout <commit>
git rev-parse HEAD
```

### 2. Install

Any CPython from 3.11 onward. The only third-party dependency is `httpx`.

CI verifies 3.11 and 3.12; 3.14 is verified locally. The harness package declares
3.9+, but this experiment has not been run there — if you only have 3.9 or 3.10,
run it anyway and tell us what happened.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install ./pii-leak-benchmark
```

Do not install the proxy. This experiment does not use it.

### 3. Run

From the repository root:

```bash
python benchmarks/reproduce_fragmentation.py --out reproduction
```

### 4. Read the result

The last line is `RESULT: all 2 policies reproduced the published reports.` on success.
The exit status is `0` on success and `1` on any mismatch.

Above it, one table per policy. These are the values to expect:

| Policy | Fidelity | Leak, single chunk | Leak, fragmented | DeltaFrag | Outcome |
| :--- | ---: | ---: | ---: | ---: | :--- |
| `chunk-local` | 1.00 | 0.125 | 1.00 | 0.875 | `fail` |
| `bounded-retention` | 1.00 | 0.125 | 0.125 | 0.00 | `fail` |

Both reports must also carry corpus digest
`30efa2eb658888448b4416bb527c9ff5ea8489a469f0bec71049eb88ac9efd3c` and inspector digest
`94262e29a492ab6a`. The script checks all of this and prints `yes` or `NO` per row.

### 5. Send back four things

1. `reproduction/chunk-local.json` and `reproduction/bounded-retention.json` — the reports
   your run produced.
2. `reproduction/reproduction-summary.json` — the comparison, plus your OS, Python version
   and the commit you ran.
3. The console output of step 3, and the exact commands you ran if they differed from the
   ones above.
4. Anything that went wrong, was unclear, or that you disagree with.

Item 4 is not a formality. A run that fails, a step that does not work on your machine, a
number that does not match, or a reading of the result you think is wrong is more useful
than a clean pass. Open a
[GitHub issue](https://github.com/ninadphalak/LLM-Shield-Proxy/issues) or send the files
directly.

### 6. Optional: run it in your own GitHub Actions

If you would rather not trust a run on your own laptop either, run it on infrastructure
neither of us controls.

1. Fork <https://github.com/ninadphalak/LLM-Shield-Proxy>.
2. In your fork, open the **Actions** tab and click **I understand my workflows, go ahead
   and enable them**. GitHub disables workflows in new forks until you do this.
3. Select **Reproducible Public Benchmark** in the left sidebar, then **Run workflow**.

No secrets, tokens or configuration are needed. The job installs one dependency from PyPI
and otherwise touches no network. Six runners report separately; each uploads its
regenerated reports as a downloadable artifact.

## Explanation

Everything below is context. None of it is needed to run the steps.

### What the two policies are

Both are reference inspectors implemented inside the benchmark. Neither is a product.
They exist to hold one variable apart from everything else.

- **`chunk-local`** inspects each streamed chunk on its own and forgets it.
- **`bounded-retention`** carries a bounded number of trailing characters from one chunk
  into the next, so a value split across a chunk boundary is still visible as one string.

They are otherwise the same inspector on the same 32-case corpus.

### What the four numbers mean

- **Fidelity** — the fraction of echo iterations where the client received the value it
  was supposed to receive. It says the policy did not break the stream.
- **Leak, single chunk** — the leak rate over the 16 cases where the protected value
  arrives whole, in one chunk. This is the baseline arm.
- **Leak, fragmented** — the leak rate over the 16 paired cases where the same value is
  split. This is the treatment arm.
- **DeltaFrag** — fragmented minus single-chunk. Zero means splitting the value changed
  nothing. A positive number is the share of values that a split made invisible to the
  inspector.

The two arms are paired case by case, so DeltaFrag is a within-corpus difference, not a
comparison of two populations.

### Why `chunk-local` reports `fail` even though it detects most values

`fail` has one narrow meaning across this lab: a protected test value reached the capture
server unmasked. `chunk-local` catches 14 of the 16 values that arrive whole (`0.125`
leaked) and catches none of the 16 that are split (`1.00` leaked). DeltaFrag `0.875` is
the whole result: 14 of 16 paired values became invisible to an otherwise working
detector purely because of where the chunk boundary fell.

`bounded-retention` leaks the same `0.125` in both arms, so its DeltaFrag is `0.00`.
Splitting the value stopped mattering. It still reports `fail` because that residual
`0.125` is a real leak, and the per-axis breakdown in the report says where: every
remaining leak is in the percent-encoded cases (`metrics.by_axis.encoding`: `plain` 0 of
22, `percent` 4 of 10). Neither policy decodes before matching. Retention fixed the
fragmentation problem and did not fix the encoding problem; these are separate axes and
the report keeps them separate. A third reference policy,
`retention-plus-decoding`, does both and is the only one of the five that reaches
`0.00` in every arm.

### What the script checks, beyond the four numbers

It does a full recursive comparison of your report against the published one, every field,
and fails on any difference outside an explicit eleven-entry ignore list. Those eleven
record when, where and how fast the run happened: the timestamp, your OS and Python
version, wall-clock latency statistics, and the ephemeral loopback port the capture server
bound to. Everything else — every rate, every digest, every per-axis marginal, the case
inventory, the outcome — must match exactly.

Your `environment` block is expected to differ from the published one. That is the report
recording your machine, which is the point of an independent run.

`tests/conformance/test_reproduce_fragmentation.py` pins that ignore list by exact set
equality, so it cannot quietly grow to cover a real field.

### Where the published numbers come from

`benchmarks/results/v2-response-split/chunk-local.json` and `bounded-retention.json`,
produced on 2026-09-09 at seed `a1b2c3d4e5f60001` under the midpoint partition oracle.
Both files are byte-identical to the copies under the `v2-evidence-round-8` tag
(commit `6cbfee3`), which is the evidence anchor the paper cites.

### This runs in CI too

The `fragmentation-reproduction` job in
[`.github/workflows/benchmark.yml`](https://github.com/ninadphalak/LLM-Shield-Proxy/blob/main/.github/workflows/benchmark.yml)
runs exactly the command in step 3 on Ubuntu, macOS and Windows, on Python 3.11 and 3.12,
and uploads the regenerated reports.

All six reproduced at commit `46f4c6d`
([run 34897865803](https://github.com/ninadphalak/LLM-Shield-Proxy/actions/runs/34897865803)).
Each job's regenerated reports are downloadable from that run, so you can diff your files
against a machine that is not the author's before reporting anything.

If your machine disagrees with all six of those, that is worth knowing and is exactly what
item 4 above is asking for.

### What a green run proves, and what it does not

Running this in your own CI raises the claim from *the author says the numbers reproduce*
to *the numbers reproduce on infrastructure the author does not control*. Concretely, a
green run in your fork shows that the published JSON in this repository is what this code
produces, that the result does not depend on hidden machine state, and that nothing
reaches the network to fetch an answer.

It does not show that the instrument is honest. The corpus, the two policies and the leak
inspector all live in this repository and were all written by the same person who
published the numbers. A rigged inspector would reproduce perfectly on six runners.

The part that needs a human reading the code rather than a green check is small, and it is
worth naming exactly:

| What to read | Where |
| :--- | :--- |
| The chunk-local policy | `ChunkLocal` in `pii-leak-benchmark/pii_leak_benchmark/v2_emitter.py`, line 379 |
| The retaining policy, including its boundary rule | `Retaining` (line 394) and `Retaining._cut` (line 414), same file |
| What both share, so the only difference is retention | `_redact_then_rehydrate` (line 339) and the `Policy` base (line 321) |
| How a leak is decided and tiered | `_leak_tier` (line 1973) |

Those four are the whole argument. If the two policies differ anywhere except retention,
the comparison is not measuring what it claims to measure, and that is a finding worth
reporting.

### What this experiment does not establish

- It is not a measurement of any product. Both policies are reference inspectors.
- It is 32 cases in four entity types and two encodings. It does not measure detector
  accuracy on real traffic.
- The fragmentation is a two-part split at the value midpoint, not every possible split
  point. The exhaustive oracles are a separate, longer run.
- Reproducing these numbers says the instrument is deterministic and the published files
  are what the code produces. It does not independently validate the method. Disagreeing
  with the method is a separate and welcome contribution.

## Related

- [Reproduce the conformance report](./reproducing) — the v1.0.0 local and HTTP profiles.
- [Published results](./results)
- [Submit a run](./submitting)
