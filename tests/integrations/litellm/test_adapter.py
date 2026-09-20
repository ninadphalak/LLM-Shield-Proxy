"""The LiteLLM adapter ships as an example artifact, not as a module of this package.

The example adapter subclasses `CustomGuardrail` and must import LiteLLM, but a bare
`pip install llm-shield-proxy` has no LiteLLM. Shipping the adapter inside the package
would break module import audits.

The dependency direction these tests defend is:
    the example       --may-import-->     litellm
    llm_shield_proxy  --never-imports-->  litellm

Tests run in subprocesses because the parent pytest process has already imported the package,
making in-process assertions vacuously true.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_DIR = REPO_ROOT / "examples" / "integrations" / "litellm"
EXAMPLE_CONFIG = EXAMPLE_DIR / "config.guardrail.yaml"
ADAPTER = EXAMPLE_DIR / "litellm_guardrail.py"

_IMPORT_LITELLM = re.compile(r"^\s*(?:from|import)\s+litellm\b", re.M)

_COUNT_IMPORTS = """
import json, sys
before = set(sys.modules)
{import_statement}
print(json.dumps(sorted(set(sys.modules) - before)))
"""


def _modules_imported_by(import_statement: str) -> set[str]:
    script = textwrap.dedent(_COUNT_IMPORTS).format(import_statement=import_statement)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return set(json.loads(result.stdout))


def test_base_install_never_reaches_the_host():
    """Importing the gateway must not import LiteLLM.

    Prevents every install from carrying LiteLLM's dependency tree for a feature only
    LiteLLM users need.
    """
    imported = _modules_imported_by("import llm_shield_proxy")
    reached = sorted(name for name in imported if name == "litellm" or name.startswith("litellm."))
    assert reached == [], f"import llm_shield_proxy reached the host framework: {reached}"


def test_no_packaged_module_imports_litellm():
    """The wheel-only audit, restated locally to catch undeclared dependencies before push."""
    packaged = sorted((REPO_ROOT / "llm_shield_proxy").rglob("*.py"))
    assert packaged, "the package tree should not be empty"

    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in packaged
        if _IMPORT_LITELLM.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"packaged modules that import LiteLLM: {offenders}"


def test_example_config_points_at_a_class_that_the_shipped_file_defines():
    """The documented dotted path must name a class that exists in the mounted file.

    A rename in the code or config would leave the `guardrail:` string stale and surface
    at the user's proxy startup. We catch it here instead.
    """
    config = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    entries = config["guardrails"]
    assert len(entries) == 1, "the example must show exactly one guardrail entry"

    dotted = entries[0]["litellm_params"]["guardrail"]
    module_name, _, class_name = dotted.rpartition(".")
    assert module_name, f"no module component in {dotted!r}"
    assert class_name, f"no class component in {dotted!r}"

    source_file = EXAMPLE_DIR / f"{module_name}.py"
    assert source_file.is_file(), f"{dotted!r} names a module with no shipped file: {source_file.name}"
    assert f"class {class_name}(" in source_file.read_text(encoding="utf-8"), (
        f"{dotted!r} names a class that {source_file.name} does not define"
    )


def test_example_config_asks_for_both_lifecycle_modes():
    """Ensures the example teaches the correct configuration.

    `pre_call` alone returns placeholders to the caller. The two modes are complements,
    not alternatives.
    """
    config = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    mode = config["guardrails"][0]["litellm_params"]["mode"]
    assert set(mode) == {"pre_call", "post_call"}, mode
