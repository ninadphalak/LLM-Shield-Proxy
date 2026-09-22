from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


class OutputPathError(ValueError):
    """Raised before a product run can write to an unsafe output location."""


def _normalized(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _is_within(path: Path, root: Path) -> bool:
    normalized_path = _normalized(path)
    normalized_root = _normalized(root)
    try:
        return os.path.commonpath((normalized_path, normalized_root)) == normalized_root
    except ValueError:
        # Different Windows drives are necessarily disjoint.
        return False


def validate_fresh_output_path(
    output: Path | str,
    *,
    repo_root: Path | str,
    frozen_roots: Iterable[Path | str] = (),
) -> Path:
    """Return a resolved fresh destination after rejecting repository aliases.

    The function performs validation only. The orchestrator creates the directory
    after every guard has passed.
    """

    output_path = Path(output).expanduser()
    repository = Path(repo_root).resolve(strict=True)
    resolved = output_path.resolve(strict=False)

    if output_path.exists() or output_path.is_symlink():
        raise OutputPathError(f"output path already exists: {output_path}")
    if _is_within(resolved, repository):
        raise OutputPathError("output must be outside the repository")

    for frozen_root in frozen_roots:
        frozen = Path(frozen_root).resolve(strict=False)
        if _is_within(resolved, frozen) or _is_within(frozen, resolved):
            raise OutputPathError("output aliases a frozen evidence location")

    return resolved
