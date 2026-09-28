"""The GitHub Action for the checker: its inputs, how it handles them, and its release path.

The action runs for real in `.github/workflows/ci.yml` (job `mcp-ssrf-check-action`) against
the test double in both modes; these tests pin what that job cannot see.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHECKER_DIST = ROOT / "mcp-ssrf-check"
ACTION = CHECKER_DIST / "action.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"

INPUTS = {
    "url": (True, None),
    "lifecycle": (False, "auto"),
    "bearer": (False, ""),
    "fetch-tool": (False, ""),
    "url-argument": (False, "url"),
    "redirect-target": (False, ""),
    "callback-host": (False, "127.0.0.1"),
    "skip": (False, ""),
    "json-out": (False, "mcp-ssrf-check-report.json"),
    "fail-on": (False, "fail"),
    "artifact-name": (False, "mcp-ssrf-check-report"),
    "python-version": (False, "3.12"),
    "source": (False, None),
}


def _action():
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


def _package_version():
    text = (CHECKER_DIST / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version = "([^"]+)"', text, re.M).group(1)


def test_input_contract():
    inputs = _action()["inputs"]
    assert set(inputs) == set(INPUTS)
    for name, (required, default) in INPUTS.items():
        assert inputs[name].get("required", False) is required, name
        if default is not None:
            assert inputs[name]["default"] == default, name


def test_default_install_is_the_released_version():
    """A caller pinning `@mcp-check-vX.Y.Z` must get checker X.Y.Z from PyPI."""
    from mcp_ssrf_check import __version__

    assert _package_version() == __version__
    assert _action()["inputs"]["source"]["default"] == f"mcp-ssrf-check=={__version__}"


def test_inputs_are_never_interpolated_into_shell_source():
    for step in _action()["runs"]["steps"]:
        if "run" in step:
            assert "${{" not in step["run"], step["name"]


def test_bearer_reaches_the_checker_through_the_environment_only():
    steps = _action()["runs"]["steps"]
    run = next(step for step in steps if step.get("id") == "run")
    assert run["env"]["MCP_SSRF_CHECK_BEARER"] == "${{ inputs.bearer }}"
    assert "--bearer" not in run["run"]
    others = [step for step in steps if step is not run]
    assert all("inputs.bearer" not in str(step) for step in others)


def test_report_is_uploaded_even_when_the_check_fails():
    upload = next(step for step in _action()["runs"]["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["if"].startswith("${{ always()")
    assert upload["with"]["path"] == "${{ inputs.json-out }}"


def test_ci_runs_the_action_from_the_tree_against_both_modes():
    job = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["mcp-ssrf-check-action"]
    steps = {step.get("id"): step for step in job["steps"]}
    for mode in ("hardened", "weak"):
        assert steps[mode]["uses"] == "./mcp-ssrf-check"
        assert steps[mode]["with"]["source"] == "./mcp-ssrf-check"
    assert steps["hardened"].get("continue-on-error") is None
    assert steps["weak"]["continue-on-error"] is True
    assertion = next(step for step in job["steps"] if step.get("name") == "Assert both outcomes")
    assert assertion["env"]["WEAK_STEP"] == "${{ steps.weak.outcome }}"
    assert 'test "$WEAK_STEP" = "failure"' in assertion["run"]


def test_release_path_is_gated_on_its_own_tag_and_checks_the_version():
    jobs = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))["jobs"]
    build = jobs["build-mcp-ssrf-check"]
    assert build["if"] == "startsWith(github.event.release.tag_name, 'mcp-check-v')"
    commands = "\n".join(step.get("run", "") for step in build["steps"])
    assert 'test "$TAG" = "mcp-check-v$version"' in commands
    assert 'test "$action_pin" = "mcp-ssrf-check==$version"' in commands
    assert jobs["publish-mcp-ssrf-check-testpypi"]["needs"] == "build-mcp-ssrf-check"
    assert jobs["publish-mcp-ssrf-check-pypi"]["needs"] == "publish-mcp-ssrf-check-testpypi"
    for name in ("publish-mcp-ssrf-check-testpypi", "publish-mcp-ssrf-check-pypi"):
        assert jobs[name]["permissions"] == {"id-token": "write"}
    # The proxy job must not also fire on this tag.
    assert not "mcp-check-v0.2.0".startswith("v")
