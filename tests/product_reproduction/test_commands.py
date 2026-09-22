from __future__ import annotations

import pytest

from benchmarks.product_reproduction.commands import CommandContractError, CommandSpec, render_command_log
from benchmarks.product_reproduction.sanitizer import SensitiveValue, build_sanitizer


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


def test_command_repr_never_exposes_arguments() -> None:
    command = CommandSpec.from_argv(["gateway", "--api-key", "synthetic-secret-value"])

    assert "synthetic-secret-value" not in repr(command)
    assert "--api-key" not in repr(command)


def test_command_log_separates_display_text_from_sanitized_argv() -> None:
    secret = "synthetic-secret-value"
    sanitizer = build_sanitizer([SensitiveValue(label="TARGET_API_KEY", value=secret)])
    rendered = render_command_log(
        displayed={"gateway": f"gateway --api-key {secret}"},
        executed={"gateway": CommandSpec.from_argv(["gateway", "--api-key", secret, "literal;not-shell"])},
        sanitizer=sanitizer,
    )

    assert secret not in rendered
    assert "<TARGET_API_KEY>" in rendered
    assert "DISPLAYED REPRODUCTION COMMANDS" in rendered
    assert "EXECUTED ARGUMENT VECTORS" in rendered
    assert '"literal;not-shell"' in rendered


def test_command_log_rejects_multiline_display_injection() -> None:
    sanitizer = build_sanitizer([])

    with pytest.raises(CommandContractError, match="control bytes"):
        render_command_log(
            displayed={"gateway": "gateway --safe\nforged: command"},
            executed={"gateway": CommandSpec.from_argv(["gateway", "--safe"])},
            sanitizer=sanitizer,
        )


@pytest.mark.parametrize("argv", [[], [""], ["gateway", "bad\0value"], ["gateway", "bad\nvalue"]])
def test_argument_vector_rejects_empty_or_control_byte_arguments(argv: list[str]) -> None:
    with pytest.raises(CommandContractError):
        CommandSpec.from_argv(argv)
