"""A supported-Python floor is a claim; CI is the only place it is tested.

llm-shield-proxy 1.6.8 declared `requires-python = ">=3.9"` and carried a `python-3.9+`
badge. Installed from PyPI into a clean virtualenv: on 3.9 the Settings class failed to
import, and on 3.10 every request answered 400, because `asyncio.timeout` is 3.11+ and the
broad handler around it reported "Malformed JSON payload". The suite had never run on either
version; the matrix in ci.yml was 3.11 and 3.12. These tests hold each distribution's floor,
classifiers and badge to the versions its CI matrix actually runs, so the claim cannot drift
from the evidence again.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomllib
import yaml

ROOT = Path(__file__).resolve().parents[1]
CI = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))

# distribution directory -> the ci.yml job whose matrix installs it and runs its tests
DISTRIBUTIONS = {
    ".": "test-and-lint",
    "pii-leak-benchmark": "benchmark-floor",
    "chunk-invariance": "chunk-invariance-python",
}


def _matrix(job: str) -> list[tuple[int, int]]:
    versions = CI["jobs"][job]["strategy"]["matrix"]["python-version"]
    return sorted(tuple(int(part) for part in str(v).split(".")) for v in versions)


def _pyproject(directory: str) -> dict:
    return tomllib.loads((ROOT / directory / "pyproject.toml").read_text(encoding="utf-8"))


def _floor(requires_python: str) -> tuple[int, int]:
    match = re.fullmatch(r">=\s*(\d+)\.(\d+)", requires_python.strip())
    assert match, f"requires-python should be a plain floor such as >=3.11, not {requires_python!r}"
    return int(match.group(1)), int(match.group(2))


@pytest.mark.parametrize(("directory", "job"), sorted(DISTRIBUTIONS.items()))
def test_requires_python_is_the_lowest_version_ci_runs(directory: str, job: str) -> None:
    project = _pyproject(directory)["project"]
    assert _floor(project["requires-python"]) == _matrix(job)[0], (
        f"{project['name']} claims {project['requires-python']} but the {job} matrix starts at "
        f"{'.'.join(map(str, _matrix(job)[0]))}; test the floor or lower the claim"
    )


@pytest.mark.parametrize(("directory", "job"), sorted(DISTRIBUTIONS.items()))
def test_version_classifiers_are_exactly_the_versions_ci_runs(directory: str, job: str) -> None:
    project = _pyproject(directory)["project"]
    classified = sorted(
        tuple(int(part) for part in c.rsplit("::", 1)[1].strip().split("."))
        for c in project["classifiers"]
        if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", c)
    )
    assert classified == _matrix(job), f"{project['name']}: classifiers {classified} vs CI matrix {_matrix(job)}"


def test_the_readme_badge_states_the_proxy_floor() -> None:
    floor = _floor(_pyproject(".")["project"]["requires-python"])
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    badge = re.search(r"img\.shields\.io/badge/python-(\d+)\.(\d+)%2B", readme)
    assert badge, "the README carries a python-X.Y+ badge"
    assert (int(badge.group(1)), int(badge.group(2))) == floor
