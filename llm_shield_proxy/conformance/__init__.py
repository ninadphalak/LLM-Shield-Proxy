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
- ``provenance`` - the self-reported run metadata block, standard library only.
- ``artifact`` - report writing, standard library only.
- ``redaction_claim`` - what a published row is allowed to say, standard library only.

**The HTTP profile must stay light.** ``local`` imports the reference proxy's
detector, vault and streaming engines, which is the entire third-party dependency
tree. Importing it here eagerly meant that measuring somebody else's gateway
required installing this gateway's full stack, which is the concrete blocker on
third-party runs. ``run_conformance`` and ``write_conformance_report`` are therefore
resolved lazily through the module ``__getattr__`` below, and ``build_attestation``
comes from ``provenance``, not from ``local``. Do not restore a module-level
``from llm_shield_proxy.conformance.local import ...`` here; a regression test
asserts the light import path stays light.

The audit-chain verifier the harness calls (``compliance.report.verify_worm_log``)
is product code and deliberately lives outside this package.
"""

from typing import Any

from llm_shield_proxy.conformance.artifact import write_conformance_report
from llm_shield_proxy.conformance.http_profile import run_http_conformance
from llm_shield_proxy.conformance.provenance import build_attestation

# Only the in-process local profile needs the proxy engines. Everything the HTTP
# profile touches -- the runner, the report writer, the attestation block -- is
# stdlib + httpx and is imported eagerly here.
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
    "run_http_conformance",
    "write_conformance_report",
]
