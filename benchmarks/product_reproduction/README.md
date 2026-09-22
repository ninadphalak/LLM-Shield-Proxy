# Released product reproduction contracts

This directory contains the checked-in contracts for measuring released proxy products. The
measurement runner is separate from the product under test and acquires only reviewed release
sources selected by catalog ID.

The initial contract layer provides:

- a strict product and release-source catalog;
- an allowlisted Python adapter lifecycle;
- versioned bundle, comparison, and submission schemas; and
- a fresh-output guard that keeps generated evidence outside the repository and frozen results.

`catalog.json` is intentionally empty until a product adapter, configuration, release source, and
accepted baseline have been reviewed together. Catalog data cannot supply commands, runner labels,
credentials, or arbitrary adapter imports.

Generated evidence must use a new directory outside the repository. Product baselines are read-only
inputs beneath an explicitly allowed baseline root. Nothing in this package writes to
`benchmarks/results/`.

Run the contract tests from the repository root:

```text
python -m pytest tests/product_reproduction -q
```

The orchestration, bundle builder, and first released-product adapter are added in later bounded
changes. Until then, this package defines and validates contracts but does not claim to reproduce a
product result.
