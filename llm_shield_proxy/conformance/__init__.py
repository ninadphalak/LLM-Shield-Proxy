"""Streaming Privacy Gateway conformance harness.

This subpackage is **evaluation tooling, not proxy runtime code**. Nothing here is
imported on a request path. It exists to measure whether a streaming LLM gateway --
this one or any other OpenAI-compatible implementation -- redacts personal data
before forwarding a request upstream, and to emit a report against the published
schema in ``spec/v1.0.0/``.

It ships inside the installed package on purpose: a third party must be able to
``pip install llm-shield-proxy`` and run the harness against their own gateway, in
their own CI, without vendoring this repository.

- ``local`` - in-process profile: correctness checks plus labeled microbenchmarks.
- ``http_profile`` - endpoint-neutral profile driving any OpenAI-compatible ``/v1``
  gateway over HTTP through a controlled capture upstream.

The audit-chain verifier the harness calls (``compliance.report.verify_worm_log``)
is product code and deliberately lives outside this package.
"""

from llm_shield_proxy.conformance.http_profile import run_http_conformance
from llm_shield_proxy.conformance.local import (
    build_attestation,
    run_conformance,
    write_conformance_report,
)

__all__ = [
    "build_attestation",
    "run_conformance",
    "run_http_conformance",
    "write_conformance_report",
]
