"""The checker must not carry the gateway: not in its imports and not in its declared dependencies.

Same rule, same reason as the harness (``tests/conformance/test_harness_install_weight.py``):
a maintainer runs this against their own server, and a checker that pulls in one vendor's
gateway is not a neutral instrument. Asserted in a subprocess because this pytest process
has already imported the gateway.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_DIST = REPO_ROOT / "mcp-ssrf-check"
CHECKER_PACKAGE = CHECKER_DIST / "mcp_ssrf_check"

HTTPX_TREE = {"anyio", "attr", "certifi", "click", "h11", "httpcore", "httpx", "idna", "pygments", "rich", "sniffio"}

_COUNT_THIRD_PARTY = """
import json, os, sys, sysconfig
sys.path.insert(0, {dist!r})
before = set(sys.modules)
import mcp_ssrf_check.cli
import mcp_ssrf_check.checks
import mcp_ssrf_check.listener
stdlib = os.path.normcase(sysconfig.get_paths()["stdlib"])
third = set()
for name, module in list(sys.modules.items()):
    if name in before or module is None:
        continue
    top = name.split(".")[0]
    if top.startswith("_") or top in before or top == "mcp_ssrf_check":
        continue
    path = getattr(sys.modules.get(top), "__file__", None)
    if path and ("site-packages" in os.path.normcase(path) or "dist-packages" in os.path.normcase(path)):
        third.add(top)
print(json.dumps(sorted(third)))
"""


def test_importing_the_checker_pulls_in_httpx_and_nothing_else():
    script = textwrap.dedent(_COUNT_THIRD_PARTY).format(dist=str(CHECKER_DIST))
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    third_party = set(json.loads(result.stdout.strip().splitlines()[-1]))
    assert third_party <= HTTPX_TREE, third_party - HTTPX_TREE


def test_no_checker_module_names_the_gateway():
    offenders = [p.name for p in CHECKER_PACKAGE.glob("*.py") if "llm_shield_proxy" in p.read_text(encoding="utf-8")]
    assert not offenders, offenders


def test_declared_dependencies_are_httpx_only():
    text = (CHECKER_DIST / "pyproject.toml").read_text(encoding="utf-8")
    block = text.split("dependencies = [", 1)[1].split("]", 1)[0]
    declared = {line.strip().strip('",').split(">=")[0] for line in block.splitlines() if line.strip().startswith('"')}
    assert declared == {"httpx"}, declared
