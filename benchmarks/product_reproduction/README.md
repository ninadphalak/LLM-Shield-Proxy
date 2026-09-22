# Released product reproduction contracts

This directory contains the checked-in contracts for measuring released proxy products. The
measurement runner is separate from the product under test and acquires only reviewed release
sources selected by catalog ID.

The initial contract layer provides:

- a strict product and release-source catalog;
- an allowlisted Python adapter lifecycle;
- versioned bundle, comparison, and submission schemas; and
- a fresh-output guard that keeps generated evidence outside the repository and frozen results.

The lifecycle foundation also provides:

- OS-assigned loopback ports with run-specific correlation IDs;
- memory, disk, container-limit, and Docker admission checks;
- bounded retries limited to release acquisition;
- a fixture-aware sanitizer configured before a target starts; and
- isolated operator and response-midpoint lifecycles with bounded diagnostics and scoped cleanup.

Adapters receive a monotonic readiness deadline and must apply it to every readiness operation.
The orchestrator rejects a readiness result returned after that deadline. Commands remain argument
vectors and are never reconstructed as shell text.

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

The bundle builder and first released-product adapter are added in later bounded changes. Until
then, this package validates the lifecycle with a test-only adapter but does not claim to reproduce
a product result.
