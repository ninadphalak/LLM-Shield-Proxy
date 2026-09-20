"""pii-leak-benchmark: does your LLM gateway send raw personal data upstream?

A neutral, endpoint-agnostic conformance harness for OpenAI-compatible streaming
gateways. It stands a controlled capture server in front of the gateway's configured
upstream, sends a prompt containing synthetic-but-valid personal data, and inspects
every channel the gateway can reach the capture through -- request line, method,
headers, chunk extensions, trailers and JSON body -- for the protected values.

It measures a gateway. It is not a gateway, and it does not depend on one. Nothing
in this distribution imports ``llm_shield_proxy``; the direction is deliberate and a
regression test enforces it. This keeps the benchmark independent of any gateway it
measures.

Install weight is a feature, not an accident: standard library plus ``httpx``. An
engineer at another gateway must not have to install a competing gateway's stack --
OpenTelemetry, redis, pydantic, a detector, an ASGI server -- to measure their own
product.

Public API:

- ``run_http_conformance`` - run the profile against a base URL, return a report.
- ``capture_session`` - hold the capture endpoint open across a target you start
  yourself, so a gateway that contacts its upstream during startup finds it there.
- ``write_conformance_report`` - write a report as LF-terminated JSON.
- ``build_attestation`` - self-reported CI provenance for a run, or ``None``.
- ``derive_outcome`` / ``rationale_for`` - what a published row is ALLOWED to say.
- ``OperatorSpecimens`` / ``findings_from_report`` / ``render`` - the explanation layer.
  Pass ``OperatorSpecimens()`` to ``run_http_conformance`` to receive the run's generated
  values; they are never written to the report. ``render(..., reveal=False)`` publishes
  shapes instead. See ``explain`` for the rule.

Reports validate against the Streaming Privacy Gateway (SPG) report schema in
``spec/v1.0.0`` of the LLM-Shield-Proxy repository. The specification keeps the SPG
name; only this tool carries the searchable one.
"""

from pii_leak_benchmark.artifact import write_conformance_report
from pii_leak_benchmark.explain import (
    Finding,
    OperatorSpecimens,
    findings_from_report,
    published_dict,
    render,
)
from pii_leak_benchmark.http_profile import (
    CaptureSession,
    CaptureUnreachableError,
    capture_session,
    run_http_conformance,
)
from pii_leak_benchmark.provenance import build_attestation, source_revision
from pii_leak_benchmark.redaction_claim import derive_outcome, rationale_for

__all__ = [
    "CaptureSession",
    "CaptureUnreachableError",
    "Finding",
    "OperatorSpecimens",
    "build_attestation",
    "capture_session",
    "derive_outcome",
    "findings_from_report",
    "published_dict",
    "rationale_for",
    "render",
    "run_http_conformance",
    "source_revision",
    "write_conformance_report",
]

__version__ = "0.4.0"
