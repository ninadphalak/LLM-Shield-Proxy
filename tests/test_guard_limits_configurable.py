"""The guard batch bounds are operator configuration, not constants.

A gateway that posts a whole conversation per request cannot use the guard at
all while these are hardcoded: the adapter turns the 413 into a refused request.
These tests pin the contract — upstream defaults, environment override,
rejection of nonsensical values, and unchanged 413 behaviour at the boundary.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from llm_shield_proxy.core.config import Settings

LIMIT_VARS = ("GUARD_MAX_TEXTS_PER_REQUEST", "GUARD_MAX_TOTAL_TEXT_CHARS")


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Assert code defaults, never a developer's shell or a checked-in .env."""
    for name in LIMIT_VARS:
        monkeypatch.delenv(name, raising=False)


def make_settings(**env) -> Settings:
    return Settings(_env_file=None, **env)


def test_defaults_are_the_historical_values():
    settings = make_settings()
    assert settings.GUARD_MAX_TEXTS_PER_REQUEST == 256
    assert settings.GUARD_MAX_TOTAL_TEXT_CHARS == 1_000_000


def test_env_vars_override(monkeypatch):
    monkeypatch.setenv("GUARD_MAX_TEXTS_PER_REQUEST", "4096")
    monkeypatch.setenv("GUARD_MAX_TOTAL_TEXT_CHARS", "8000000")
    settings = make_settings()
    assert settings.GUARD_MAX_TEXTS_PER_REQUEST == 4096
    assert settings.GUARD_MAX_TOTAL_TEXT_CHARS == 8_000_000


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number", ""])
def test_invalid_values_are_rejected(monkeypatch, value):
    monkeypatch.setenv("GUARD_MAX_TOTAL_TEXT_CHARS", value)
    with pytest.raises((ValidationError, ValueError)):
        make_settings()


def test_guard_router_binds_the_settings():
    """The router must use the settings, not private copies of the numbers."""
    pytest.importorskip("fastapi")
    from llm_shield_proxy.api import guard_router as module

    defaults = make_settings()
    assert module.MAX_TEXTS_PER_REQUEST == defaults.GUARD_MAX_TEXTS_PER_REQUEST
    assert module.MAX_TOTAL_TEXT_CHARS == defaults.GUARD_MAX_TOTAL_TEXT_CHARS


def test_too_many_texts_is_still_a_413():
    pytest.importorskip("fastapi")
    from llm_shield_proxy.api import guard_router as module

    texts, error = module._extract_texts({"texts": ["x"] * (module.MAX_TEXTS_PER_REQUEST + 1)})
    assert texts is None
    assert error.status_code == 413


def test_too_many_characters_is_still_a_413():
    pytest.importorskip("fastapi")
    from llm_shield_proxy.api import guard_router as module

    texts, error = module._extract_texts({"texts": ["x" * (module.MAX_TOTAL_TEXT_CHARS + 1)]})
    assert texts is None
    assert error.status_code == 413


def test_within_bounds_passes():
    pytest.importorskip("fastapi")
    from llm_shield_proxy.api import guard_router as module

    texts, error = module._extract_texts({"texts": ["hello" * 100]})
    assert error is None
    assert texts == ["hello" * 100]
