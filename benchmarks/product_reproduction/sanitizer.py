from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from urllib.parse import quote

LABEL_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


class SanitizerConfigurationError(ValueError):
    """Sensitive values cannot be transformed into an unambiguous dictionary."""


@dataclass(frozen=True)
class SensitiveValue:
    label: str
    value: str = field(repr=False)


class Sanitizer:
    __slots__ = ("_replacements",)

    def __init__(self, replacements: tuple[tuple[str, str], ...]) -> None:
        self._replacements = replacements

    def __repr__(self) -> str:
        return "<Sanitizer configured>"

    def sanitize(self, text: str) -> str:
        sanitized = text
        for rendering, replacement in self._replacements:
            sanitized = sanitized.replace(rendering, replacement)
        return sanitized

    def contains_sensitive(self, text: str) -> bool:
        return any(rendering in text for rendering, _ in self._replacements)


def _renderings(value: str) -> set[str]:
    encoded = value.encode("utf-8")
    standard_base64 = base64.b64encode(encoded).decode("ascii")
    urlsafe_base64 = base64.urlsafe_b64encode(encoded).decode("ascii")
    return {
        value,
        json.dumps(value, ensure_ascii=True)[1:-1],
        quote(value, safe=""),
        standard_base64,
        standard_base64.rstrip("="),
        urlsafe_base64,
        urlsafe_base64.rstrip("="),
        encoded.hex(),
        encoded.hex().upper(),
    }


def build_sanitizer(values: list[SensitiveValue] | tuple[SensitiveValue, ...]) -> Sanitizer:
    replacements: dict[str, str] = {}
    raw_owners: dict[str, str] = {}
    for item in values:
        if not LABEL_PATTERN.fullmatch(item.label):
            raise SanitizerConfigurationError("sanitizer labels must use uppercase identifier syntax")
        if len(item.value) < 4:
            raise SanitizerConfigurationError("sensitive values shorter than four characters are unsafe")
        previous_owner = raw_owners.get(item.value)
        if previous_owner is not None and previous_owner != item.label:
            raise SanitizerConfigurationError("one sensitive value cannot have multiple labels")
        raw_owners[item.value] = item.label
        replacement = f"<{item.label}>"
        for rendering in _renderings(item.value):
            if not rendering:
                continue
            previous = replacements.get(rendering)
            if previous is not None and previous != replacement:
                raise SanitizerConfigurationError("derived sensitive renderings collide")
            replacements[rendering] = replacement
    ordered = tuple(sorted(replacements.items(), key=lambda item: (-len(item[0]), item[0])))
    return Sanitizer(ordered)
