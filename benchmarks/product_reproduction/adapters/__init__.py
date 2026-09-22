from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import ProductAdapter


AdapterFactory = Callable[[], "ProductAdapter"]

# Every production adapter must be reviewed and registered here. Test doubles are
# intentionally injected by tests and can never be selected from catalog data.
PRODUCTION_ADAPTERS: dict[str, AdapterFactory] = {}


def adapter_factory(name: str) -> AdapterFactory:
    try:
        return PRODUCTION_ADAPTERS[name]
    except KeyError as exc:
        raise KeyError(f"unknown production adapter: {name}") from exc


__all__ = ["PRODUCTION_ADAPTERS", "AdapterFactory", "adapter_factory"]
