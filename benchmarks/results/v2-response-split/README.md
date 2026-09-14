# v2 response-split profile - measured runs

This directory holds the machine-readable JSON reports. **They are the authority whenever
prose disagrees with them.**

The documentation that used to live in this file has moved to the docs site, where it is
maintained and where the result tables are generated from these reports rather than typed:

| Page | What it covers |
| :--- | :--- |
| `website/docs/conformance/benchmark_readme.md` | What the profile measures, the four numbers, and the findings a reviewer should check. |
| `website/docs/conformance/results.md` | Every published rate, generated from the JSON in this directory. CI fails if it drifts. |
| `website/docs/conformance/reproduce-fragmentation.md` | Re-derive two of these reports yourself in about two minutes, offline. |
| `website/docs/conformance/benchmark-revision-history.md` | Every instrument defect found and fixed, and the chronological re-measurement log. |

## Layout

```
*.json                            single-run reports, one per policy or gateway
seed-sweep*.json                  aggregates over 6 or 12 seeds
exhaustive-splits/                every internal two-part split, rather than the midpoint
worst-case-splits/                bounded union over partition families
exhaustive-presidio-seed-sweep/   12 seeds x 2 wrappers under the exhaustive oracle
exhaustive-llm-guard-seed-sweep/  the same for the LLM Guard wrappers
worst-case-presidio-seed-sweep/   the same under the union oracle
GATEWAY-LANDSCAPE.md              product versions and configuration provenance
```

## Regenerating the results page

Anything that changes a report in this directory must regenerate the published table:

```bash
python benchmarks/generate_results_page.py
```

CI runs it with `--check` and fails if the committed page and these reports disagree.
