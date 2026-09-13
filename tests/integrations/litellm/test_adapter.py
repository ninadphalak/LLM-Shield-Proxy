"""The LiteLLM adapter ships as an example artifact, not as a module of this package.

``examples/integrations/litellm/litellm_guardrail.py`` subclasses LiteLLM's
``CustomGuardrail``, so it imports LiteLLM at module scope. That is correct for a file the
proxy mounts and wrong for a file the wheel installs: a bare ``pip install
llm-shield-proxy`` has no LiteLLM, and a packaged module that cannot be imported is exactly
what ``tests/ootb/_import_every_module.py`` fails on. It did fail on the first version of
this change, which shipped the adapter inside the package.

So the dependency direction these tests defend is::

    the example       --may-import-->   litellm
    llm_shield_proxy  --never-imports-->  litellm

Nothing here imports LiteLLM, so the suite stays runnable without the host. The first test
runs in a SUBPROCESS on purpose: the parent pytest process has already imported this
package, so an in-process assertion would pass vacuously.
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

    If it did, every install of the gateway would carry LiteLLM's dependency tree for a
    feature only LiteLLM users need -- the same class of defect
    ``tests/conformance/test_harness_install_weight.py`` exists to catch one distribution
    over.
    """
    imported = _modules_imported_by("import llm_shield_proxy")
    reached = sorted(name for name in imported if name == "litellm" or name.startswith("litellm."))
    assert reached == [], f"import llm_shield_proxy reached the host framework: {reached}"


def test_no_packaged_module_imports_litellm():
    """The wheel-only audit, restated locally so the mistake cannot recur quietly.

    ``tests/ootb/_import_every_module.py`` imports every module of the installed package
    and fails on an undeclared dependency. That job caught the first version of this change
    after a push; this catches it before one.
    """
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

    ``guardrail:`` is the only place a user is told what to write, and the module component
    of the dotted path is a *file name inside the proxy*, not a repository path. A rename on
    either side would leave that string stale and surface it at the user's proxy startup
    rather than here.
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
    """``pre_call`` alone redacts the request and returns placeholders to the caller.

    The two modes are complements, not alternatives, so an example that lists only one of
    them teaches the misconfiguration this guardrail is easiest to make.
    """
    config = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    mode = config["guardrails"][0]["litellm_params"]["mode"]
    assert set(mode) == {"pre_call", "post_call"}, mode
