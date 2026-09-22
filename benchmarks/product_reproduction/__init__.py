"""Contracts for measuring released proxy products in isolated CI runs."""

from .catalog import ProductCatalog, load_catalog

__all__ = ["ProductCatalog", "load_catalog"]
