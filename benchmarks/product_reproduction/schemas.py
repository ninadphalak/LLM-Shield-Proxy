from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "spec" / "product-reproduction" / "v1"
SCHEMA_FILES: Mapping[str, str] = MappingProxyType(
    {
        "catalog": "catalog.schema.json",
        "bundle-manifest": "bundle-manifest.schema.json",
        "comparison": "comparison.schema.json",
        "submission": "submission.schema.json",
    }
)
FORMAT_CHECKER = FormatChecker()


@FORMAT_CHECKER.checks("date-time", raises=(TypeError, ValueError))
def _is_date_time(value: object) -> bool:
    if not isinstance(value, str):
        return True
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    return parsed.tzinfo is not None


class SchemaContractError(ValueError):
    """A requested contract is unknown or its checked-in schema cannot load."""


@lru_cache(maxsize=len(SCHEMA_FILES))
def _load_schema(name: str) -> dict[str, Any]:
    filename = SCHEMA_FILES.get(name)
    if filename is None:
        raise SchemaContractError(f"unknown product-reproduction schema: {name}")
    try:
        document = json.loads((SCHEMA_ROOT / filename).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaContractError(f"cannot load product-reproduction schema {name}: {exc}") from exc
    Draft202012Validator.check_schema(document)
    return document


@lru_cache(maxsize=len(SCHEMA_FILES))
def schema_validator(name: str) -> Draft202012Validator:
    """Return the allowlisted validator with draft formats enforced as assertions."""

    return Draft202012Validator(_load_schema(name), format_checker=FORMAT_CHECKER)
