from __future__ import annotations

import pytest

from benchmarks.product_reproduction.commands import CommandContractError, CommandSpec


def test_windows_path_and_shell_metacharacters_remain_literal_arguments() -> None:
    command = CommandSpec.from_argv(
        [
            r"C:\Program Files\Gateway\gateway.exe",
            "--config",
            r"C:\run data\config.json",
            "literal;$(not-a-shell)|value",
        ]
    )

    assert command.argv[0] == r"C:\Program Files\Gateway\gateway.exe"
    assert command.argv[2] == r"C:\run data\config.json"
    assert command.argv[3] == "literal;$(not-a-shell)|value"


@pytest.mark.parametrize("argv", [[], [""], ["gateway", "bad\0value"], ["gateway", "bad\nvalue"]])
def test_argument_vector_rejects_empty_or_control_byte_arguments(argv: list[str]) -> None:
    with pytest.raises(CommandContractError):
        CommandSpec.from_argv(argv)
