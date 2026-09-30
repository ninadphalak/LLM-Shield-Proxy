"""Run provenance for conformance reports. Standard library only to maintain neutrality.

``build_attestation`` reads CI environment variables. It avoids dependencies
to ensure the HTTP profile remains lightweight and independently installable.
"""

from __future__ import annotations

import os
import platform
from typing import Any, Optional


def source_revision() -> str:
    return os.getenv("GITHUB_SHA") or os.getenv("PII_LEAK_BENCHMARK_SOURCE_REVISION") or "unknown"


def build_attestation() -> Optional[dict[str, Any]]:
    """Self-reported provenance for this run, or None if no CI context exists."""
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
