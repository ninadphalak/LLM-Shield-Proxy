"""The reference implementation's own local conformance profile.

This subpackage is **evaluation tooling, not proxy runtime code**. Nothing here is
imported on a request path. It runs the in-process profile: correctness checks over
this proxy's detector, vault and streaming engines, plus labeled microbenchmarks.

**The neutral, endpoint-agnostic harness is no longer here.** It is a separate
distribution, ``pii-leak-benchmark`` (import package ``pii_leak_benchmark``), and it
carries the neutral name deliberately: a benchmark named after one of the products it
scores cannot referee them. The direction of dependency is one-way and enforced by
test -- this package may import the benchmark, the benchmark never imports this one.

To measure a gateway over HTTP, including this one::

    pip install pii-leak-benchmark
    pii-leak-benchmark --target-base-url http://127.0.0.1:8899/v1

``run_conformance`` stays lazy: it imports the proxy's engines, and callers that only
want the report writer or the attestation block should not pay for that import.
"""

from typing import Any

from pii_leak_benchmark.artifact import write_conformance_report
from pii_leak_benchmark.provenance import build_attestation

_LAZY = {
    "run_conformance": "llm_shield_proxy.conformance.local",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))


__all__ = [
    "build_attestation",
    "run_conformance",
    "write_conformance_report",
]
