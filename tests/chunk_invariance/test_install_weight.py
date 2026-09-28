"""The library must stay dependency-free: no third-party import, no gateway, nothing declared.

Asserted in a subprocess because this pytest process has already imported the gateway.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_DIST = REPO_ROOT / "chunk-invariance"
LIBRARY_PACKAGE = LIBRARY_DIST / "chunk_invariance"

_COUNT_THIRD_PARTY = """
import json, os, sys
sys.path.insert(0, {dist!r})
before = set(sys.modules)
import chunk_invariance
import chunk_invariance.examples
third = set()
for name, module in list(sys.modules.items()):
    if name in before or module is None:
        continue
    top = name.split(".")[0]
    if top.startswith("_") or top in before or top == "chunk_invariance":
        continue
    path = getattr(sys.modules.get(top), "__file__", None)
    if path and ("site-packages" in os.path.normcase(path) or "dist-packages" in os.path.normcase(path)):
        third.add(top)
print(json.dumps(sorted(third)))
"""


def test_importing_the_library_pulls_in_nothing_third_party():
    script = textwrap.dedent(_COUNT_THIRD_PARTY).format(dist=str(LIBRARY_DIST))
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


def test_no_module_names_the_gateway():
    offenders = [p.name for p in LIBRARY_PACKAGE.glob("*.py") if "llm_shield_proxy" in p.read_text(encoding="utf-8")]
    assert not offenders, offenders


def test_only_the_strategies_module_imports_hypothesis():
    importers = sorted(p.name for p in LIBRARY_PACKAGE.glob("*.py") if "import hypothesis" in p.read_text(encoding="utf-8") or "from hypothesis" in p.read_text(encoding="utf-8"))
    assert importers == ["strategies.py"], importers


def test_declares_no_runtime_dependencies():
    text = (LIBRARY_DIST / "pyproject.toml").read_text(encoding="utf-8")
    assert "\ndependencies = []\n" in text
    assert 'hypothesis = ["hypothesis>=6"]' in text


def test_version_is_declared_once_per_file_and_agrees():
    import chunk_invariance

    text = (LIBRARY_DIST / "pyproject.toml").read_text(encoding="utf-8")
    assert f'\nversion = "{chunk_invariance.__version__}"\n' in text


JS_DIST = REPO_ROOT / "chunk-invariance-js"


def test_the_typescript_package_has_no_runtime_dependencies():
    manifest = json.loads((JS_DIST / "package.json").read_text(encoding="utf-8"))
    for field in ("dependencies", "peerDependencies", "optionalDependencies", "bundleDependencies"):
        assert not manifest.get(field), field
    assert manifest["files"] == ["dist", "README.md", "LICENSE"]


def test_the_typescript_sources_import_only_each_other():
    import re

    for path in (JS_DIST / "src").glob("*.ts"):
        for spec in re.findall(r'from\s+"([^"]+)"', path.read_text(encoding="utf-8")):
            assert spec.startswith("./"), f"{path.name} imports {spec}"
