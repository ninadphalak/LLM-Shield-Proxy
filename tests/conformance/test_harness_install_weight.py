"""Running the HTTP profile must not require installing the reference proxy.

``conformance/__init__.py`` used to import ``local`` eagerly, and ``local`` imports the
proxy's detector, vault and streaming engines -- so ``import
llm_shield_proxy.conformance.http_profile`` pulled OpenTelemetry, redis, cryptography,
pydantic, google-re2, faker and the rest behind it. Making the import lazy is not
enough on its own: ``run_http_conformance`` called ``build_attestation`` from ``local``
on every run, and that function reads only ``GITHUB_*``/``RUNNER_*`` environment
variables. It now lives in ``conformance.provenance``, which is standard library only.

Asking an engineer at another gateway to install a competing gateway's full stack in
order to run a neutral benchmark is the concrete blocker on third-party runs, which is
the whole point of publishing the harness.

Each test runs in a SUBPROCESS. The parent pytest process has already imported most of
the package, so an in-process assertion would pass vacuously.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The floor: httpx's own dependency tree. The profile itself needs nothing beyond it.
HTTPX_TREE = {"click", "httpx", "idna", "pygments", "rich"}

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
third.discard("llm_shield_proxy")
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
    import json

    return set(json.loads(result.stdout.strip().splitlines()[-1]))


def test_http_profile_import_needs_nothing_beyond_httpx():
    """stdlib plus httpx. Not the proxy's dependency tree."""
    observed = _third_party_for("import llm_shield_proxy.conformance.http_profile")
    assert observed <= HTTPX_TREE, sorted(observed - HTTPX_TREE)


def test_public_conformance_api_import_needs_nothing_beyond_httpx():
    """The documented entry point, not just the private module."""
    observed = _third_party_for(
        "from llm_shield_proxy.conformance import run_http_conformance, build_attestation"
    )
    assert observed <= HTTPX_TREE, sorted(observed - HTTPX_TREE)


def test_running_the_http_profile_does_not_import_the_proxy_engines():
    """``build_attestation`` on the run path must not drag ``local`` back in.

    This is the half that a lazy ``__init__`` alone would miss: the import graph can be
    clean while the first actual run re-imports everything.
    """
    script = textwrap.dedent(
        """
        import sys
        from llm_shield_proxy.conformance.http_profile import (
            CaptureUnreachableError, run_http_conformance,
        )
        try:
            # No target and no capture reachable is fine: the attestation and report
            # paths are what matter, and the probe runs before any target traffic.
            run_http_conformance(
                "http://127.0.0.1:1/v1", iterations=1, capture_port=0, timeout_seconds=1.0
            )
        except Exception:
            pass
        heavy = [name for name in ("redis", "cryptography", "pydantic", "faker", "re2",
                                   "opentelemetry", "yaml", "orjson", "psutil")
                 if name in sys.modules]
        print("HEAVY:" + ",".join(heavy))
        print("LOCAL:" + str("llm_shield_proxy.conformance.local" in sys.modules))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    output = result.stdout
    heavy_line = next(line for line in output.splitlines() if line.startswith("HEAVY:"))
    assert heavy_line == "HEAVY:", heavy_line
    local_line = next(line for line in output.splitlines() if line.startswith("LOCAL:"))
    assert local_line == "LOCAL:False", local_line


def test_build_attestation_is_available_without_the_proxy_engines():
    script = textwrap.dedent(
        """
        import os, sys
        os.environ["LLM_SHIELD_SOURCE_REVISION"] = "abc123"
        from llm_shield_proxy.conformance.provenance import build_attestation
        block = build_attestation()
        assert block["commit_sha"] == "abc123", block
        assert block["verification"] == "self-reported", block
        assert "llm_shield_proxy.conformance.local" not in sys.modules
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip().endswith("OK")


def test_local_profile_still_re_exports_build_attestation():
    """Backwards compatibility for callers that imported it from ``local``."""
    from llm_shield_proxy.conformance.local import build_attestation as from_local
    from llm_shield_proxy.conformance.provenance import build_attestation as canonical

    assert from_local is canonical


# --------------------------------------------------------------------------
# The DECLARED dependency set, not just the import graph
# --------------------------------------------------------------------------

PROXY_ONLY_PACKAGES = (
    "fastapi", "uvicorn", "pydantic", "pydantic-settings", "redis", "faker",
    "cryptography", "google-re2", "opentelemetry-api", "opentelemetry-sdk",
    "orjson", "watchdog", "prometheus-client", "grpclib", "betterproto", "pyyaml",
)


def test_declared_base_dependencies_are_httpx_only():
    """pyproject must not put the reference proxy in the default install.

    The import graph was already clean while `pip install llm-shield-proxy` still
    installed 20 packages, so reading the import graph is not evidence about the
    install. This reads the declaration; the venv test below reads reality.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        pytest.skip("tomllib requires Python 3.11+")
    manifest = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    base = manifest["project"]["dependencies"]
    assert [item.split(">")[0].split("[")[0] for item in base] == ["httpx"], base
    # The proxy set must still be installable, just not by default.
    proxy_extra = manifest["project"]["optional-dependencies"]["proxy"]
    names = {item.split(">")[0].split("[")[0] for item in proxy_extra}
    assert {"fastapi", "uvicorn", "cryptography", "opentelemetry-api"} <= names
    # `pip install -e .[dev]` must still yield a working proxy for the test suite.
    dev_extra = manifest["project"]["optional-dependencies"]["dev"]
    assert any("llm-shield-proxy[proxy]" in item for item in dev_extra), dev_extra
    # And the harness needs a console script that does not import the ASGI server.
    scripts = manifest["project"]["scripts"]
    assert scripts["llm-shield-conformance"] == "llm_shield_proxy.cli:conformance_main"


def test_cli_http_profile_does_not_import_the_proxy():
    """The documented COMMAND, not just the module.

    `benchmark_main` used to import `core.config.settings` and pull
    `write_conformance_report` through `conformance/__init__`, which resolved
    `local` -- so the CLI path imported 26 third-party packages and the whole
    reference proxy even though the module import graph was clean. That is the
    defect this test exists to catch.
    """
    script = textwrap.dedent(
        """
        import os, sys
        os.environ["TELEMETRY_ENABLED"] = "false"
        from llm_shield_proxy.cli import benchmark_main
        try:
            benchmark_main([
                "--target-base-url", "http://127.0.0.1:1/v1", "--iterations", "1",
                "--capture-port", "0", "--json-out", os.devnull,
            ])
        except SystemExit:
            pass
        heavy = [n for n in ("redis", "cryptography", "pydantic", "faker", "re2",
                             "opentelemetry", "yaml", "orjson", "fastapi", "uvicorn",
                             "psutil")
                 if n in sys.modules]
        print("HEAVY:" + ",".join(heavy))
        print("LOCAL:" + str("llm_shield_proxy.conformance.local" in sys.modules))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    lines = result.stdout.splitlines()
    assert next(line for line in lines if line.startswith("HEAVY:")) == "HEAVY:"
    assert next(line for line in lines if line.startswith("LOCAL:")) == "LOCAL:False"


def test_local_profile_still_re_exports_write_conformance_report():
    from llm_shield_proxy.conformance.artifact import (
        write_conformance_report as canonical,
    )
    from llm_shield_proxy.conformance.local import (
        write_conformance_report as from_local,
    )

    assert from_local is canonical


def test_base_install_runs_the_http_profile_in_a_clean_virtualenv(tmp_path):
    """Install the base package into a fresh venv and RUN the profile from it.

    Reading pyproject is not verification. Last round the import graph was clean while
    `pip install llm-shield-proxy` still installed 20 packages AND the CLI still pulled
    26 -- both passed every test in this file at the time.

    Opt-in because it builds a wheel and hits the network. Set SHIELD_REQUIRE_VENV=1 to
    turn a missing prerequisite into a failure, following the repo convention that a
    green build must never mean "nothing ran".
    """
    import os
    import venv

    required = os.getenv("SHIELD_REQUIRE_VENV") == "1"
    if not required and os.getenv("SHIELD_TEST_VENV") != "1":
        pytest.skip("set SHIELD_TEST_VENV=1 (or SHIELD_REQUIRE_VENV=1) to run")

    target = tmp_path / "venv"
    venv.create(target, with_pip=True)
    python = target / "Scripts" / "python.exe"
    if not python.exists():
        python = target / "bin" / "python"

    install = subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", str(REPO_ROOT)],
        capture_output=True, text=True, timeout=900,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    listing = subprocess.run(
        [str(python), "-m", "pip", "list", "--format=freeze"],
        capture_output=True, text=True, timeout=120,
    ).stdout.lower()
    for package in PROXY_ONLY_PACKAGES:
        assert f"{package}==" not in listing, f"{package} reached the base install"

    # And it must actually work, not merely import.
    check = subprocess.run(
        [
            str(python), "-c",
            "from llm_shield_proxy.conformance import run_http_conformance;"
            "from llm_shield_proxy.cli import conformance_main;"
            "print('OK')",
        ],
        capture_output=True, text=True, timeout=300,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert "OK" in check.stdout
