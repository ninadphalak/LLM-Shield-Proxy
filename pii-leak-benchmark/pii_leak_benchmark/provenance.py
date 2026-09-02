"""Run provenance for conformance reports. Standard library only, on purpose.

``build_attestation`` reads ``GITHUB_*``/``RUNNER_*`` environment variables and
nothing else. It lived in ``local``, which imports the reference proxy's detector,
vault and streaming engines, so every HTTP-profile run against a third-party gateway
dragged the whole proxy dependency tree in behind one env-var read. The HTTP profile
must stay installable as stdlib plus ``httpx``: asking an engineer to install a
competing gateway's full stack to run a neutral benchmark is a blocker on getting
outside runs at all.

Nothing in this distribution may import from ``llm_shield_proxy``. The benchmark is the
neutral measurer; the proxy is one of the things it measures.
"""

from __future__ import annotations

import os
import platform
from typing import Any, Optional


def source_revision() -> str:
    return os.getenv("GITHUB_SHA") or os.getenv("PII_LEAK_BENCHMARK_SOURCE_REVISION") or "unknown"


def build_attestation() -> Optional[dict[str, Any]]:
    """Provenance for this run, or None when there is no CI context to report.

    Every value here is read from the run environment, so it is SELF-REPORTED: it
    records who says they ran the harness, and is forgeable by whoever ran it. It is
    not third-party attestation. Only a mechanism a verifier can check without
    trusting the submitter (GitHub OIDC, Sigstore) may set another verification value.
    """
    commit_sha = os.getenv("GITHUB_SHA") or os.getenv("PII_LEAK_BENCHMARK_SOURCE_REVISION")
    if not commit_sha:
        return None
    attestation: dict[str, Any] = {
        "verification": "self-reported",
        "runner": os.getenv("RUNNER_NAME") or os.getenv("RUNNER_OS") or platform.node() or "unknown",
        "commit_sha": commit_sha,
    }
    repository = os.getenv("GITHUB_REPOSITORY")
    run_id = os.getenv("GITHUB_RUN_ID")
    if os.getenv("GITHUB_ACTIONS"):
        attestation["ci_provider"] = "github-actions"
    if repository:
        attestation["repository"] = repository
        if run_id:
            server = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
            attestation["run_url"] = f"{server}/{repository}/actions/runs/{run_id}"
    workflow_ref = os.getenv("GITHUB_WORKFLOW_REF")
    if workflow_ref:
        attestation["workflow_ref"] = workflow_ref
    return attestation
