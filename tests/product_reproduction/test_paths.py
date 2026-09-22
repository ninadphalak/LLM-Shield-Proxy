from __future__ import annotations

import os
from pathlib import Path

import pytest

from benchmarks.product_reproduction.paths import OutputPathError, validate_fresh_output_path


def test_accepts_fresh_output_outside_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    output = tmp_path / "artifacts" / "run-123"

    assert validate_fresh_output_path(output, repo_root=repo) == output.resolve()
    assert not output.exists()


@pytest.mark.parametrize("relative", ["benchmarks/results/run", "output/run"])
def test_rejects_output_inside_repository(tmp_path: Path, relative: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(OutputPathError, match="outside the repository"):
        validate_fresh_output_path(repo / relative, repo_root=repo)


def test_rejects_existing_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    output = tmp_path / "existing"
    output.mkdir()

    with pytest.raises(OutputPathError, match="already exists"):
        validate_fresh_output_path(output, repo_root=repo)


def test_rejects_symlink_alias_into_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    destination = repo / "generated"
    destination.mkdir(parents=True)
    alias = tmp_path / "alias"
    try:
        os.symlink(destination, alias, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(OutputPathError, match="outside the repository"):
        validate_fresh_output_path(alias / "run", repo_root=repo)


def test_rejects_output_aliasing_frozen_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    frozen = tmp_path / "published"
    frozen.mkdir()

    with pytest.raises(OutputPathError, match="frozen evidence"):
        validate_fresh_output_path(frozen / "run", repo_root=repo, frozen_roots=(frozen,))
