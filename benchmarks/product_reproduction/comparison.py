from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .schemas import schema_validator

JSONValue = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
ComparisonLevel = Literal["exact", "primary", "none"]
ComparisonStatus = Literal["matched", "drifted", "not-compared", "invalid"]
POINTER_PATTERN = re.compile(r"^/(?:[^~/]|~0|~1)+(?:/(?:[^~/]|~0|~1)+)*$")


class ComparisonError(ValueError):
    """A report or comparison policy cannot produce trustworthy comparison evidence."""


class BaselineChangedError(ComparisonError):
    """The accepted baseline changed after it was snapshotted."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reject_json_constant(value: str) -> None:
    raise ValueError(value)


def canonical_json_bytes(document: JSONValue | Mapping[str, Any]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _load_json_object(data: bytes, *, label: str) -> dict[str, JSONValue]:
    try:
        document = json.loads(
            data.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ComparisonError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise ComparisonError(f"{label} must be a JSON object")
    return document


@dataclass(frozen=True)
class ReportSnapshot:
    source: Path
    sha256: str
    raw_bytes: bytes

    @classmethod
    def read(cls, path: Path, *, expected_sha256: str | None = None) -> ReportSnapshot:
        source = path.resolve(strict=True)
        raw = source.read_bytes()
        digest = sha256_bytes(raw)
        if expected_sha256 is not None and digest != expected_sha256:
            raise ComparisonError("report hash does not match the declared SHA-256")
        _load_json_object(raw, label="report")
        return cls(source=source, sha256=digest, raw_bytes=raw)

    def assert_source_unchanged(self) -> None:
        try:
            current_digest = sha256_bytes(self.source.read_bytes())
        except OSError as exc:
            raise BaselineChangedError("snapshotted baseline is no longer readable") from exc
        if current_digest != self.sha256:
            raise BaselineChangedError("snapshotted baseline changed during the run")


@dataclass(frozen=True)
class ComparisonPolicy:
    ignored_paths: frozenset[str]
    primary_paths: frozenset[str]

    def __post_init__(self) -> None:
        if not self.primary_paths:
            raise ComparisonError("comparison policy must declare at least one primary path")
        for pointer in self.ignored_paths | self.primary_paths:
            if not POINTER_PATTERN.fullmatch(pointer):
                raise ComparisonError("comparison paths must be non-root JSON pointers")
        overlap = any(
            _at_or_below(ignored, primary) or _at_or_below(primary, ignored)
            for ignored in self.ignored_paths
            for primary in self.primary_paths
        )
        if overlap:
            raise ComparisonError("a comparison path cannot be both ignored and primary")


@dataclass(frozen=True)
class ProfileComparison:
    status: ComparisonStatus
    level: ComparisonLevel
    differences: tuple[str, ...]
    current_sha256: str | None = None
    baseline_sha256: str | None = None

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "status": self.status,
            "level": self.level,
            "differences": list(self.differences),
        }
        if self.current_sha256 is not None:
            document["current_sha256"] = self.current_sha256
        if self.baseline_sha256 is not None:
            document["baseline_sha256"] = self.baseline_sha256
        return document


def _escape_pointer(value: str) -> str:
    if not value:
        raise ComparisonError("empty JSON object keys cannot be represented by this comparison policy")
    return value.replace("~", "~0").replace("/", "~1")


def _join_pointer(parent: str, child: str) -> str:
    return f"{parent}/{_escape_pointer(child)}"


def _recursive_differences(current: JSONValue, baseline: JSONValue, pointer: str = "") -> set[str]:
    if type(current) is not type(baseline):
        if not pointer:
            raise ComparisonError("report roots must have the same object type")
        return {pointer}
    if isinstance(current, dict) and isinstance(baseline, dict):
        differences: set[str] = set()
        for key in sorted(set(current) | set(baseline)):
            child = _join_pointer(pointer, key)
            if key not in current or key not in baseline:
                differences.add(child)
            else:
                differences.update(_recursive_differences(current[key], baseline[key], child))
        return differences
    if isinstance(current, list) and isinstance(baseline, list):
        differences = set()
        for index in range(max(len(current), len(baseline))):
            child = _join_pointer(pointer, str(index))
            if index >= len(current) or index >= len(baseline):
                differences.add(child)
            else:
                differences.update(_recursive_differences(current[index], baseline[index], child))
        return differences
    if current != baseline:
        if not pointer:
            raise ComparisonError("report roots must be JSON objects")
        return {pointer}
    return set()


def _at_or_below(pointer: str, ancestor: str) -> bool:
    return pointer == ancestor or pointer.startswith(ancestor + "/")


def _pointer_exists(document: Mapping[str, JSONValue], pointer: str) -> bool:
    current: JSONValue = dict(document)
    for encoded in pointer.removeprefix("/").split("/"):
        part = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if part not in current:
                return False
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return False
    return True


def compare_profile(
    current: ReportSnapshot,
    baseline: ReportSnapshot | None,
    *,
    policy: ComparisonPolicy,
    identity_matches: bool,
    configuration_matches: bool,
    current_valid: bool = True,
    baseline_valid: bool = True,
) -> ProfileComparison:
    if baseline is None:
        return ProfileComparison(status="not-compared", level="none", differences=(), current_sha256=current.sha256)
    baseline.assert_source_unchanged()
    if not current_valid or not baseline_valid:
        return ProfileComparison(
            status="invalid",
            level="none",
            differences=(),
            current_sha256=current.sha256,
            baseline_sha256=baseline.sha256,
        )
    current_document = _load_json_object(current.raw_bytes, label="current report")
    baseline_document = _load_json_object(baseline.raw_bytes, label="baseline report")
    for pointer in policy.primary_paths:
        if not _pointer_exists(current_document, pointer) or not _pointer_exists(baseline_document, pointer):
            raise ComparisonError(f"required primary comparison path is missing: {pointer}")

    raw_differences = _recursive_differences(current_document, baseline_document)
    differences = tuple(
        sorted(
            pointer
            for pointer in raw_differences
            if not any(_at_or_below(pointer, ignored) for ignored in policy.ignored_paths)
        )
    )
    if not differences and identity_matches and configuration_matches:
        return ProfileComparison("matched", "exact", (), current.sha256, baseline.sha256)

    primary_drift = (
        not identity_matches
        or not configuration_matches
        or any(any(_at_or_below(pointer, primary) for primary in policy.primary_paths) for pointer in differences)
    )
    return ProfileComparison(
        status="drifted" if primary_drift else "matched",
        level="primary",
        differences=differences,
        current_sha256=current.sha256,
        baseline_sha256=baseline.sha256,
    )


def comparison_document(
    *,
    target_id: str,
    mode: Literal["measure", "reproduce"],
    operator: ProfileComparison,
    response_midpoint: ProfileComparison,
    baseline_id: str | None = None,
    artifact_identity_match: bool | None = None,
    configuration_match: bool | None = None,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": "pii-leak-benchmark/product-comparison/v1",
        "policy": "product-reproduction/v1",
        "target_id": target_id,
        "mode": mode,
        "profiles": {
            "operator": operator.to_document(),
            "response-midpoint": response_midpoint.to_document(),
        },
    }
    if mode == "reproduce":
        if baseline_id is None or artifact_identity_match is None or configuration_match is None:
            raise ComparisonError("reproduce comparison requires baseline and subject identity results")
        document.update(
            baseline_id=baseline_id,
            artifact_identity_match=artifact_identity_match,
            configuration_match=configuration_match,
        )
    elif any(value is not None for value in (baseline_id, artifact_identity_match, configuration_match)):
        raise ComparisonError("measure comparison cannot claim baseline subject identity")
    schema_validator("comparison").validate(document)
    return document
