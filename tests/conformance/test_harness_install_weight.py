"""The benchmark must not carry the gateway -- in its imports, its install, or its name.

``pii-leak-benchmark`` is a separate distribution from ``llm-shield-proxy`` because a
benchmark named after one of the products it scores cannot referee them, and because
asking an engineer at another gateway to install a competing gateway's full stack in
order to measure their own product is the concrete blocker on third-party runs.

The dependency direction is one-way and it is the thing these tests defend:

    llm-shield-proxy  --may-use-->  pii-leak-benchmark
    pii-leak-benchmark  --never-->  llm-shield-proxy

History, because each defect here survived the test that was supposed to catch it:
the harness's ``__init__`` once imported the local profile eagerly, which dragged in
OpenTelemetry, redis, cryptography, pydantic, google-re2 and faker; making the import
lazy was not enough because ``run_http_conformance`` still called ``build_attestation``
from that module on every run; and the import graph was clean for a whole round while
``pip install`` still pulled 20 packages and the CLI still pulled 26. Import graph,
declared dependencies and a real installation are three different claims. All three
are asserted below.

Each subprocess test runs in a SUBPROCESS on purpose. The parent pytest process has
already imported the proxy, so an in-process assertion would pass vacuously.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIST = REPO_ROOT / "pii-leak-benchmark"
BENCHMARK_PACKAGE = BENCHMARK_DIST / "pii_leak_benchmark"

# The floor: httpx's own dependency tree. The profile itself needs nothing beyond it.
# Listed generously (a venv with httpx[cli] present pulls click/rich/pygments) because
# the assertion is a subset check -- what matters is that nothing else appears.
HTTPX_TREE = {
    "anyio",
    "certifi",
    "click",
    "h11",
    "httpcore",
    "httpx",
    "idna",
    "pygments",
    "rich",
    "sniffio",
}

_COUNT_THIRD_PARTY = """
import json, os, sys, sysconfig
before = set(sys.modules)
{import_statement}
stdlib = os.path.normcase(sysconfig.get_paths()["stdlib"])
third = set()
for name, module in list(sys.modules.items()):
    if name in before or module is None:
        continue
    top = name.split(".")[0]
    if top.startswith("_") or top in before:
        continue
    parent = sys.modules.get(top)
    path = getattr(parent, "__file__", None)
    if not path:
        continue
    path = os.path.normcase(path)
    if "site-packages" in path or "dist-packages" in path:
        third.add(top)
third.discard("pii_leak_benchmark")
print(json.dumps(sorted(third)))
"""


def _third_party_for(import_statement):
    script = textwrap.dedent(_COUNT_THIRD_PARTY).format(import_statement=import_statement)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return set(json.loads(result.stdout.strip().splitlines()[-1]))


# --------------------------------------------------------------------------
# The import graph
# --------------------------------------------------------------------------


def test_http_profile_import_needs_nothing_beyond_httpx():
    """stdlib plus httpx. Not the proxy's dependency tree."""
    observed = _third_party_for("import pii_leak_benchmark.http_profile")
    assert observed <= HTTPX_TREE, sorted(observed - HTTPX_TREE)


def test_public_api_import_needs_nothing_beyond_httpx():
    """The documented entry point, not just the private module."""
    observed = _third_party_for(
        "from pii_leak_benchmark import run_http_conformance, build_attestation, "
        "write_conformance_report"
    )
    assert observed <= HTTPX_TREE, sorted(observed - HTTPX_TREE)


def test_cli_import_needs_nothing_beyond_httpx():
    observed = _third_party_for("import pii_leak_benchmark.cli")
    assert observed <= HTTPX_TREE, sorted(observed - HTTPX_TREE)


def test_no_module_in_the_benchmark_mentions_the_proxy_package():
    """Structural, so it fails on the line that introduces it, not on a heavy run.

    A conditional or lazily-imported reference would still make the neutral measurer
    depend on one of the things it measures.
    """
    offenders = []
    for source in sorted(BENCHMARK_PACKAGE.glob("*.py")):
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or '``llm_shield_proxy``' in stripped:
                continue  # prose in a docstring stating the rule is not a violation
            if "llm_shield_proxy" in stripped:
                offenders.append(f"{source.name}:{number}: {stripped}")
    assert not offenders, offenders


def test_running_the_profile_does_not_import_the_proxy_at_all():
    """The half a lazy import alone would miss: a clean graph, a heavy first run."""
    script = textwrap.dedent(
        """
        import sys
        from pii_leak_benchmark import run_http_conformance
        try:
            # No target and no capture reachable is fine: the attestation and report
            # paths are what matter, and the probe runs before any target traffic.
            run_http_conformance(
                "http://127.0.0.1:1/v1", iterations=1, capture_port=0, timeout_seconds=1.0
            )
        except Exception:
            pass
        heavy = [name for name in ("redis", "cryptography", "pydantic", "faker", "re2",
                                   "opentelemetry", "yaml", "orjson", "psutil",
                                   "fastapi", "uvicorn")
                 if name in sys.modules]
        print("HEAVY:" + ",".join(heavy))
        print("PROXY:" + str(any(n == "llm_shield_proxy" or n.startswith("llm_shield_proxy.")
                                 for n in sys.modules)))
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    lines = result.stdout.splitlines()
    assert next(line for line in lines if line.startswith("HEAVY:")) == "HEAVY:"
    assert next(line for line in lines if line.startswith("PROXY:")) == "PROXY:False"


def test_console_script_does_not_import_the_proxy():
    """The documented COMMAND, not just the module."""
    script = textwrap.dedent(
        """
        import os, sys
        from pii_leak_benchmark.cli import main
        try:
            main(["--target-base-url", "http://127.0.0.1:1/v1", "--iterations", "1",
                  "--capture-port", "0", "--json-out", os.devnull,
                  "--timeout-seconds", "1"])
        except SystemExit:
            pass
        print("PROXY:" + str(any(n == "llm_shield_proxy" or n.startswith("llm_shield_proxy.")
                                 for n in sys.modules)))
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert "PROXY:False" in result.stdout


def test_build_attestation_is_available_without_the_proxy():
    script = textwrap.dedent(
        """
        import os, sys
        # GITHUB_SHA wins over the override by design, and it is set on every GitHub
        # runner -- the same ambient-environment trap that made three other tests in
        # this repository pass locally and fail in CI.
        os.environ.pop("GITHUB_SHA", None)
        os.environ["PII_LEAK_BENCHMARK_SOURCE_REVISION"] = "abc123"
        from pii_leak_benchmark.provenance import build_attestation
        block = build_attestation()
        assert block["commit_sha"] == "abc123", block
        assert block["verification"] == "self-reported", block
        assert "llm_shield_proxy" not in sys.modules
        print("OK")
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert result.stdout.strip().endswith("OK")


# --------------------------------------------------------------------------
# The DECLARED dependency sets, not just the import graph
# --------------------------------------------------------------------------


def _manifest(path):
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        pytest.skip("tomllib requires Python 3.11+")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _names(requirements):
    return [item.split(">")[0].split("=")[0].split("[")[0].strip() for item in requirements]


def test_benchmark_declares_httpx_and_nothing_else():
    manifest = _manifest(BENCHMARK_DIST / "pyproject.toml")
    assert manifest["project"]["name"] == "pii-leak-benchmark"
    assert _names(manifest["project"]["dependencies"]) == ["httpx"]
    scripts = manifest["project"]["scripts"]
    assert scripts == {"pii-leak-benchmark": "pii_leak_benchmark.cli:main"}


def test_the_benchmark_never_depends_on_the_thing_it_measures():
    """Including through an extra. This is the neutrality claim, in one assertion."""
    manifest = _manifest(BENCHMARK_DIST / "pyproject.toml")
    declared = list(manifest["project"]["dependencies"])
    for extra in manifest["project"].get("optional-dependencies", {}).values():
        declared.extend(extra)
    assert not [item for item in declared if "llm-shield" in item.lower()], declared


def test_installing_the_proxy_installs_the_gateway_again():
    """`pip install llm-shield-proxy` must give you the proxy.

    For one unpublished round the base install was the harness and the gateway sat
    behind a `[proxy]` extra. That is the packaging this split replaced.
    """
    manifest = _manifest(REPO_ROOT / "pyproject.toml")
    base = set(_names(manifest["project"]["dependencies"]))
    assert {"fastapi", "uvicorn", "cryptography", "opentelemetry-api"} <= base, sorted(base)
    # The one-way dependency: the proxy may use the benchmark.
    assert "pii-leak-benchmark" in base
    assert "proxy" not in manifest["project"].get("optional-dependencies", {})
    # And no console script of the proxy's may claim to be the neutral harness.
    assert set(manifest["project"]["scripts"]) == {"llm-shield-proxy"}


def test_proxy_benchmark_subcommand_points_at_the_neutral_harness():
    """The old `--target-base-url` path answers with the new command, not a traceback."""
    from llm_shield_proxy.cli import benchmark_main

    code = benchmark_main(["--target-base-url", "http://127.0.0.1:1/v1"])
    assert code == 2


def test_local_profile_still_re_exports_the_shared_helpers():
    """The proxy's local profile writes its report with the benchmark's writer."""
    from pii_leak_benchmark.artifact import write_conformance_report as canonical_writer
    from pii_leak_benchmark.provenance import build_attestation as canonical_attestation

    from llm_shield_proxy.conformance import build_attestation, write_conformance_report
    from llm_shield_proxy.conformance.local import (
        build_attestation as from_local_attestation,
    )
    from llm_shield_proxy.conformance.local import (
        write_conformance_report as from_local_writer,
    )

    assert write_conformance_report is canonical_writer
    assert build_attestation is canonical_attestation
    assert from_local_writer is canonical_writer
    assert from_local_attestation is canonical_attestation


# --------------------------------------------------------------------------
# A real installation, which is the only one of the three that is evidence
# --------------------------------------------------------------------------

PROXY_PACKAGES = (
    "llm-shield-proxy", "fastapi", "uvicorn", "pydantic", "redis", "faker",
    "cryptography", "google-re2", "opentelemetry-api", "orjson", "watchdog",
    "prometheus-client", "grpclib", "betterproto", "pyyaml",
)


def _venv_python(target):
    python = target / "Scripts" / "python.exe"
    return python if python.exists() else target / "bin" / "python"


def test_benchmark_installs_and_runs_in_a_clean_virtualenv(tmp_path):
    """Install the benchmark into a fresh venv and RUN it from there.

    Reading pyproject is not verification. The import graph was clean for a whole
    round while `pip install` still pulled 20 packages, and both facts passed every
    test in this file at the time.

    Opt-in because it builds a wheel and hits the network. Set SHIELD_REQUIRE_VENV=1
    to turn a missing prerequisite into a failure, following the repo convention that
    a green build must never mean "nothing ran".
    """
    import os
    import venv

    required = os.getenv("SHIELD_REQUIRE_VENV") == "1"
    if not required and os.getenv("SHIELD_TEST_VENV") != "1":
        pytest.skip("set SHIELD_TEST_VENV=1 (or SHIELD_REQUIRE_VENV=1) to run")

    target = tmp_path / "venv"
    venv.create(target, with_pip=True)
    python = _venv_python(target)

    install = subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", str(BENCHMARK_DIST)],
        capture_output=True, text=True, timeout=900,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    listing = subprocess.run(
        [str(python), "-m", "pip", "list", "--format=freeze"],
        capture_output=True, text=True, timeout=120,
    ).stdout.lower()
    for package in PROXY_PACKAGES:
        assert f"{package}==" not in listing, f"{package} reached the benchmark install"

    # And it must WORK, not merely import: the negative control, end to end, from a
    # virtualenv that has never heard of the proxy. capture://self is raw
    # pass-through, so a correct harness reports a leak and exits 1.
    report_path = tmp_path / "control.json"
    script = _venv_python(target).parent / ("pii-leak-benchmark.exe" if os.name == "nt" else "pii-leak-benchmark")
    control = subprocess.run(
        [str(script), "--target-base-url", "capture://self", "--iterations", "1",
         "--capture-port", "0", "--json-out", str(report_path)],
        capture_output=True, text=True, timeout=600,
    )
    assert control.returncode == 1, control.stdout + control.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is False
    assert report["checks"]["configured_upstream_boundary"]["passed"] is False
    assert sorted(report["checks"]["configured_upstream_boundary"]["leaked_entity_types"])
