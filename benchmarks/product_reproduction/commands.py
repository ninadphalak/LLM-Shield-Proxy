from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


class CommandContractError(ValueError):
    """An argument vector is empty or contains a process-boundary control byte."""


@dataclass(frozen=True)
class CommandSpec:
    """A subprocess argument vector that is never reconstructed as shell source."""

    argv: tuple[str, ...]

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
