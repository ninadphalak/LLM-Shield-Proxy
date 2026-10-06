"""The secret fixtures must never reach a verification endpoint when this file is scanned.

Two checks. The first needs nothing installed: every fixture value line carries the detect-secrets
allowlist pragma, so a scan skips the line before any verification can run. The second runs when
detect-secrets is importable: a scan of the registry source with the filters the command-line
scanner installs by default reports nothing.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pii_leak_benchmark import needle_registry

PRAGMA = "# pragma: allowlist secret"


def _registry_lines() -> list[str]:
    return Path(inspect.getsourcefile(needle_registry)).read_text(encoding="utf-8").splitlines()


def _value_lines(needle) -> list[str]:
    """Source lines that contain the first line of the fixture value."""
    first = needle.value.splitlines()[0]
    return [ln for ln in _registry_lines() if first in ln]


def test_every_secret_fixture_value_line_is_allowlisted():
    for needle in needle_registry.BY_CLASS["secret"]:
        hits = _value_lines(needle)
        assert hits, f"{needle.id}: value line not found in the registry source"
        for ln in hits:
            assert ln.rstrip().endswith(PRAGMA), (
                f"{needle.id}: fixture line lacks {PRAGMA!r}: {ln.strip()[:60]}"
            )


def test_scanner_with_cli_default_filters_reports_nothing():
    pytest.importorskip("detect_secrets")
    from detect_secrets.core.plugins.util import get_mapping_from_secret_type_to_class
    from detect_secrets.core.secrets_collection import SecretsCollection
    from detect_secrets.settings import transient_settings

    plugins = [{"name": cls.__name__} for cls in get_mapping_from_secret_type_to_class().values()]
    filters = [
        {"path": "detect_secrets.filters.allowlist.is_line_allowlisted"},
        {"path": "detect_secrets.filters.common.is_ignored_due_to_verification_policies", "min_level": 2},
    ]
    with transient_settings({"plugins_used": plugins, "filters_used": filters}):
        collection = SecretsCollection()
        collection.scan_file(inspect.getsourcefile(needle_registry))
        found = [(secret.type, secret.line_number) for _, secret in collection]
    assert found == [], f"a scan of the registry would reach a verifier for: {found}"


def test_registry_docstring_names_the_controls():
    doc = needle_registry.__doc__ or ""
    assert PRAGMA in doc
    assert "test_needle_registry_scanner_safety" in doc
    assert "never presents a fixture" in doc
