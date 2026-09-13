"""The LiteLLM adapter is a host-provided module, not a dependency of this package.

``llm_shield_proxy/integrations/litellm/guardrail.py`` subclasses LiteLLM's
``CustomGuardrail``, so LiteLLM has to be importable for LiteLLM to load it by
dotted path. The direction of that dependency is what these tests defend:

    litellm  --may-import-->  llm_shield_proxy.integrations.litellm.guardrail
    llm_shield_proxy  --never-imports-->  litellm

If the second line broke, every install of the gateway would carry LiteLLM's
dependency tree for a feature only LiteLLM users need -- the same class of defect
``tests/conformance/test_harness_install_weight.py`` exists to catch one
distribution over.

Nothing here imports LiteLLM, so the suite stays runnable without the host. The
first test runs in a SUBPROCESS on purpose: the parent pytest process has already
imported this package, so an in-process assertion would pass vacuously.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_CONFIG = REPO_ROOT / "examples" / "integrations" / "litellm" / "config.guardrail.yaml"

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


@pytest.mark.parametrize(
    "statement",
    ["import llm_shield_proxy", "import llm_shield_proxy.integrations"],
)
def test_base_install_never_reaches_the_host(statement):
    """Importing this package, or its integrations subpackage, must not import LiteLLM.

    The adapter module itself does import LiteLLM, and that is correct -- it is
    dead weight in a process that has no LiteLLM. What must never happen is the
    subpackage's ``__init__`` pulling it in for everybody.
    """
    imported = _modules_imported_by(statement)
    reached = sorted(name for name in imported if name == "litellm" or name.startswith("litellm."))
    assert reached == [], f"{statement} reached the host framework: {reached}"


def test_example_config_points_at_a_class_that_exists():
    """The documented dotted path must resolve to a real class in this package.

    The example config is the only place a user is told what to write for
    ``guardrail:``. Renaming the adapter class would leave that string stale, and
    the failure would surface in the user's proxy startup rather than here.
    """
    config = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    entries = config["guardrails"]
    assert len(entries) == 1, "the example must show exactly one guardrail entry"

    dotted = entries[0]["litellm_params"]["guardrail"]
    module_path, _, class_name = dotted.rpartition(".")
    assert module_path, f"no module component in {dotted!r}"
    assert class_name, f"no class component in {dotted!r}"

    source_file = REPO_ROOT / Path(*module_path.split(".")).with_suffix(".py")
    assert source_file.is_file(), f"{dotted!r} points at missing {source_file.relative_to(REPO_ROOT)}"
    assert f"class {class_name}(" in source_file.read_text(encoding="utf-8"), (
        f"{dotted!r} names a class that {source_file.name} does not define"
    )


def test_example_config_asks_for_both_lifecycle_modes():
    """``pre_call`` alone redacts the request and returns placeholders to the caller.

    The two modes are complements, not alternatives, so an example that lists only
    one of them teaches the misconfiguration this guardrail is easiest to make.
    """
    config = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    mode = config["guardrails"][0]["litellm_params"]["mode"]
    assert set(mode) == {"pre_call", "post_call"}, mode
