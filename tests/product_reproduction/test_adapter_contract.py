from __future__ import annotations

import inspect

import pytest

from benchmarks.product_reproduction.adapters import PRODUCTION_ADAPTERS, adapter_factory
from benchmarks.product_reproduction.adapters.base import ProductAdapter
from tests.product_reproduction.fake_adapter import FakeGatewayAdapter

EXPECTED_LIFECYCLE = {
    "validate_host",
    "acquire",
    "render_config",
    "start",
    "wait_ready",
    "assert_identity",
    "measure_operator",
    "measure_response_midpoint",
    "collect_diagnostics",
    "stop",
}


def test_adapter_contract_covers_the_complete_lifecycle() -> None:
    abstract_methods = {
        name
        for name, method in inspect.getmembers(ProductAdapter, predicate=inspect.isfunction)
        if getattr(method, "__isabstractmethod__", False)
    }

    assert abstract_methods == EXPECTED_LIFECYCLE


def test_fake_adapter_is_instantiable_but_never_production_registered() -> None:
    adapter = FakeGatewayAdapter()

    assert adapter.events == []
    assert "fake" not in PRODUCTION_ADAPTERS
    with pytest.raises(KeyError, match="unknown production adapter"):
        adapter_factory("fake")
