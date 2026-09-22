from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from .sanitizer import Sanitizer

LABEL_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class CommandContractError(ValueError):
    """An argument vector is empty or contains a process-boundary control byte."""


@dataclass(frozen=True)
class CommandSpec:
    """A subprocess argument vector that is never reconstructed as shell source."""

    argv: tuple[str, ...] = field(repr=False)

    @classmethod
    def from_argv(cls, argv: Sequence[str]) -> CommandSpec:
        normalized = tuple(argv)
        if not normalized:
            raise CommandContractError("command argument vector cannot be empty")
        for argument in normalized:
            if not isinstance(argument, str):
                raise CommandContractError("command arguments must be strings")
            if not argument or "\0" in argument or "\r" in argument or "\n" in argument:
                raise CommandContractError("command arguments cannot be empty or contain control bytes")
        return cls(argv=normalized)


def render_command_log(
    *,
    displayed: dict[str, str],
    executed: dict[str, CommandSpec],
    sanitizer: Sanitizer,
) -> str:
    """Render sanitized human commands separately from exact executed argv arrays."""

    if set(displayed) != set(executed):
        raise CommandContractError("displayed and executed command labels must match")
    if any(not LABEL_PATTERN.fullmatch(label) for label in displayed):
        raise CommandContractError("command labels must be lowercase safe identifiers")
    if any("\0" in command or "\r" in command or "\n" in command for command in displayed.values()):
        raise CommandContractError("displayed commands cannot contain control bytes")
    lines = ["DISPLAYED REPRODUCTION COMMANDS"]
    for label in sorted(displayed):
        lines.append(f"{label}: {sanitizer.sanitize(displayed[label])}")
    lines.append("")
    lines.append("EXECUTED ARGUMENT VECTORS")
    for label in sorted(executed):
        argv = [sanitizer.sanitize(argument) for argument in executed[label].argv]
        lines.append(f"{label}: {json.dumps(argv, ensure_ascii=False, separators=(',', ':'))}")
    return "\n".join(lines) + "\n"
