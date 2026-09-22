from __future__ import annotations

import base64
import json
from urllib.parse import quote

import pytest

from benchmarks.product_reproduction.sanitizer import (
    SanitizerConfigurationError,
    SensitiveValue,
    build_sanitizer,
)


def test_sanitizer_covers_fixture_and_credential_renderings() -> None:
    fixture = 'alice+fixture@example.test/"quoted"'
    credential = "virtual-key-123456"
    sanitizer = build_sanitizer(
        [
            SensitiveValue(label="FIXTURE_EMAIL", value=fixture),
            SensitiveValue(label="TARGET_API_KEY", value=credential),
        ]
    )
    renderings = [
        fixture,
        json.dumps(fixture)[1:-1],
        quote(fixture, safe=""),
        base64.b64encode(fixture.encode()).decode(),
        base64.urlsafe_b64encode(fixture.encode()).decode().rstrip("="),
        fixture.encode().hex(),
        credential,
    ]

    sanitized = sanitizer.sanitize(" | ".join(renderings))

    assert fixture not in sanitized
    assert credential not in sanitized
    assert sanitized.count("<FIXTURE_EMAIL>") >= 6
    assert "<TARGET_API_KEY>" in sanitized


def test_sanitizer_covers_unpadded_standard_base64_when_it_differs_from_urlsafe() -> None:
    fixture = "fixture-sensitive-value?>"
    standard = base64.b64encode(fixture.encode()).decode().rstrip("=")
    urlsafe = base64.urlsafe_b64encode(fixture.encode()).decode().rstrip("=")
    assert standard != urlsafe

    sanitized = build_sanitizer([SensitiveValue(label="FIXTURE_VALUE", value=fixture)]).sanitize(standard)

    assert sanitized == "<FIXTURE_VALUE>"


def test_sanitizer_dictionary_is_not_exposed_by_repr() -> None:
    sensitive = SensitiveValue(label="TARGET_API_KEY", value="virtual-key-123456")
    sanitizer = build_sanitizer([sensitive])

    assert "virtual-key-123456" not in repr(sanitizer)
    assert "TARGET_API_KEY" not in repr(sanitizer)
    assert "virtual-key-123456" not in repr(sensitive)


@pytest.mark.parametrize(
    "values",
    [
        [SensitiveValue(label="bad-label", value="long-enough")],
        [SensitiveValue(label="TOKEN", value="abc")],
        [SensitiveValue(label="ONE", value="same-value"), SensitiveValue(label="TWO", value="same-value")],
    ],
)
def test_sanitizer_rejects_ambiguous_or_unsafe_configuration(values: list[SensitiveValue]) -> None:
    with pytest.raises(SanitizerConfigurationError):
        build_sanitizer(values)
