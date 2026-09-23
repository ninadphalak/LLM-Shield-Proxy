from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from .schemas import SchemaContractError, schema_validator

PRODUCT_ROOT = Path("benchmarks/product_reproduction")
ALLOWED_RELEASE_HOSTS = {
    "github-releases": "api.github.com",
    "pypi-json": "pypi.org",
}
MUTABLE_REFERENCE = re.compile(r"(?<![A-Za-z0-9])(?:main-latest|latest)(?![A-Za-z0-9])", re.IGNORECASE)
NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*][^)]*\)[+*{]")
CONFIG_PLACEHOLDER = re.compile(r"^\{\{[A-Z][A-Z0-9_]*\}\}$")
SENSITIVE_CONFIG_KEY = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)
MAX_CONFIG_BYTES = 262_144
MAX_CONFIG_DEPTH = 64


class CatalogError(ValueError):
    """A catalog is structurally invalid or violates a reviewed semantic rule."""


@dataclass(frozen=True)
class ServiceLimit:
    service: str
    memory_mib: int


@dataclass(frozen=True)
class RunnerRequirements:
    runner_class: str
    minimum_memory_mib: int
    minimum_disk_mib: int
    service_limits: tuple[ServiceLimit, ...]
    larger_runner_variable: str | None = None


@dataclass(frozen=True)
class CanaryPolicy:
    enabled: bool
    interval_days: int


@dataclass(frozen=True)
class AcceptedBaseline:
    id: str
    released_version: str
    artifact_reference: str
    artifact_identity: str
    reports: Mapping[str, str | None]
    primary_outcomes: tuple[str, ...]
    comparison_policy: str
    accepted_by_pr: str
    canary: CanaryPolicy


@dataclass(frozen=True)
class ReleaseExclusions:
    prereleases: bool
    drafts: bool
    yanked: bool
    release_candidates: bool


@dataclass(frozen=True)
class ReleaseAssetRules:
    name_pattern: str
    media_types: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseSource:
    id: str
    project_id: str
    project_url: str
    source_type: str
    api_base_url: str
    repository: str | None
    package: str | None
    version_pattern: str
    exclude: ReleaseExclusions
    assets: ReleaseAssetRules
    container_registry: str | None
    container_repository: str | None
    pagination_limit: int
    maximum_response_bytes: int
    checksum_policy: str
    eligible_adapters: tuple[str, ...]
    eligible_configurations: tuple[str, ...]


@dataclass(frozen=True)
class ProductTarget:
    id: str
    project: str
    project_url: str
    license: str
    artifact_kind: str
    adapter: str
    release_source: str
    configuration_id: str
    configuration_path: str
    architecture: str
    duty: str
    model: str
    profiles: tuple[str, ...]
    dispatchable: bool
    runner: RunnerRequirements
    accepted_baselines: tuple[AcceptedBaseline, ...]

    def baseline(self, baseline_id: str) -> AcceptedBaseline | None:
        return next((item for item in self.accepted_baselines if item.id == baseline_id), None)


@dataclass(frozen=True)
class ProductCatalog:
    schema: str
    release_sources: tuple[ReleaseSource, ...]
    targets: tuple[ProductTarget, ...]

    @property
    def dispatchable_ids(self) -> tuple[str, ...]:
        return tuple(target.id for target in self.targets if target.dispatchable)

    def target(self, target_id: str) -> ProductTarget:
        for target in self.targets:
            if target.id == target_id:
                return target
        raise CatalogError(f"unknown target id: {target_id}")


def _schema_error(document: Mapping[str, Any]) -> None:
    try:
        validator = schema_validator("catalog")
    except SchemaContractError as exc:
        raise CatalogError(str(exc)) from exc
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    if not errors:
        return
    error = errors[0]
    location = ".".join(str(part) for part in error.absolute_path) or "<root>"
    raise CatalogError(f"catalog schema validation failed at {location}: {error.message}")


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _normalized(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((_normalized(path), _normalized(root))) == _normalized(root)
    except ValueError:
        return False


def _repo_path(value: str, *, field: str, repo_root: Path) -> Path:
    if Path(value).is_absolute() or PureWindowsPath(value).is_absolute() or ".." in PurePosixPath(value).parts:
        raise CatalogError(f"{field} must be a repository-relative path")
    return (repo_root / Path(PurePosixPath(value))).resolve(strict=False)


def _validate_https_url(value: str, *, field: str, expected_host: str | None = None) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CatalogError(f"{field} must be a credential-free HTTPS URL without query or fragment")
    if expected_host and parsed.hostname.lower() != expected_host:
        raise CatalogError(f"{field} host must be {expected_host}")


def _validate_safe_pattern(value: str, *, field: str) -> None:
    if not value.startswith("^") or not value.endswith("$"):
        raise CatalogError(f"{field} must be anchored")
    if len(value) > 160 or "(?" in value or "\\1" in value or NESTED_QUANTIFIER.search(value):
        raise CatalogError(f"{field} uses unsupported or unsafe regular-expression features")
    try:
        re.compile(value)
    except re.error as exc:
        raise CatalogError(f"{field} is invalid: {exc}") from exc


def _validate_configuration(path: Path, *, configuration_id: str) -> None:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_CONFIG_BYTES + 1)
    except OSError as exc:
        raise CatalogError(f"cannot read configuration_path: {exc}") from exc
    if len(raw) > MAX_CONFIG_BYTES:
        raise CatalogError("configuration_path exceeds maximum size")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CatalogError("configuration_path must contain bounded valid JSON") from exc
    if not isinstance(document, dict):
        raise CatalogError("configuration_path root must be an object")
    if document.get("schema") != "pii-leak-benchmark/product-configuration/v1":
        raise CatalogError("configuration_path has unknown schema")
    if document.get("configuration_id") != configuration_id:
        raise CatalogError("configuration_path configuration_id does not match target")

    pending: list[tuple[object, int, tuple[str, ...], bool]] = [(document, 1, (), False)]
    while pending:
        value, depth, path, sensitive_value = pending.pop()
        if depth > MAX_CONFIG_DEPTH:
            raise CatalogError("configuration_path exceeds maximum JSON depth")
        if isinstance(value, dict):
            pending.extend(
                (
                    item,
                    depth + 1,
                    path + (str(item_key),),
                    sensitive_value
                    or bool(
                        path
                        and path[-1] == "environment"
                        and SENSITIVE_CONFIG_KEY.search(str(item_key))
                    ),
                )
                for item_key, item in value.items()
            )
        elif isinstance(value, list):
            pending.extend((item, depth + 1, path, sensitive_value) for item in value)
        elif isinstance(value, str):
            if Path(value).is_absolute() or PureWindowsPath(value).is_absolute():
                raise CatalogError("configuration_path contains a local absolute path")
            if (
                sensitive_value
                and value
                and not CONFIG_PLACEHOLDER.fullmatch(value)
            ):
                raise CatalogError("configuration_path contains a literal credential value")


def _validate_semantics(
    document: Mapping[str, Any],
    *,
    repo_root: Path,
    adapter_names: set[str],
    allowed_baseline_roots: tuple[Path, ...],
) -> None:
    sources = document["release_sources"]
    targets = document["targets"]
    duplicate_source_ids = _duplicates(source["id"] for source in sources)
    if duplicate_source_ids:
        raise CatalogError(f"duplicate release source id: {sorted(duplicate_source_ids)[0]}")
    duplicate_target_ids = _duplicates(target["id"] for target in targets)
    if duplicate_target_ids:
        raise CatalogError(f"duplicate target id: {sorted(duplicate_target_ids)[0]}")

    source_by_id = {source["id"]: source for source in sources}
    product_root = (repo_root / PRODUCT_ROOT).resolve(strict=False)
    baseline_roots = tuple(root.resolve(strict=False) for root in allowed_baseline_roots)

    for source in sources:
        expected_host = ALLOWED_RELEASE_HOSTS[source["source_type"]]
        _validate_https_url(source["api_base_url"], field="api_base_url", expected_host=expected_host)
        _validate_https_url(source["project_url"], field="project_url")
        _validate_safe_pattern(source["version_pattern"], field="version_pattern")
        _validate_safe_pattern(source["assets"]["name_pattern"], field="assets.name_pattern")

    baseline_ids: list[str] = []
    for target in targets:
        if target["adapter"] not in adapter_names:
            raise CatalogError(f"unknown adapter: {target['adapter']}")
        source = source_by_id.get(target["release_source"])
        if source is None:
            raise CatalogError(f"unknown release_source: {target['release_source']}")
        if target["adapter"] not in source["eligible_adapters"]:
            raise CatalogError("release source eligible_adapters does not include target adapter")
        if target["configuration_id"] not in source["eligible_configurations"]:
            raise CatalogError("release source eligible_configurations does not include target configuration")
        if target["project_url"] != source["project_url"]:
            raise CatalogError("target project_url must equal the reviewed release-source project_url")

        service_ids = [item["service"] for item in target["runner"]["service_limits"]]
        duplicate_service_ids = _duplicates(service_ids)
        if duplicate_service_ids:
            raise CatalogError(f"duplicate runner service id: {sorted(duplicate_service_ids)[0]}")

        config_path = _repo_path(target["configuration_path"], field="configuration_path", repo_root=repo_root)
        if not _is_within(config_path, product_root) or not config_path.is_file():
            raise CatalogError("configuration_path must resolve to a checked-in file under product reproduction")
        _validate_configuration(config_path, configuration_id=target["configuration_id"])

        for baseline in target["accepted_baselines"]:
            baseline_ids.append(baseline["id"])
            if MUTABLE_REFERENCE.search(baseline["artifact_reference"]):
                raise CatalogError("accepted baseline has a mutable artifact_reference")
            for profile, report in baseline["reports"].items():
                if report is None:
                    continue
                report_path = _repo_path(report, field="baseline report", repo_root=repo_root)
                if not report_path.is_file() or not any(_is_within(report_path, root) for root in baseline_roots):
                    raise CatalogError(f"baseline report for {profile} is outside allowed evidence roots")

    duplicate_baseline_ids = _duplicates(baseline_ids)
    if duplicate_baseline_ids:
        raise CatalogError(f"duplicate baseline id: {sorted(duplicate_baseline_ids)[0]}")


def _make_catalog(document: Mapping[str, Any]) -> ProductCatalog:
    release_sources = tuple(
        ReleaseSource(
            id=source["id"],
            project_id=source["project_id"],
            project_url=source["project_url"],
            source_type=source["source_type"],
            api_base_url=source["api_base_url"],
            repository=source.get("repository"),
            package=source.get("package"),
            version_pattern=source["version_pattern"],
            exclude=ReleaseExclusions(**source["exclude"]),
            assets=ReleaseAssetRules(
                name_pattern=source["assets"]["name_pattern"],
                media_types=tuple(source["assets"]["media_types"]),
            ),
            container_registry=source.get("container_registry"),
            container_repository=source.get("container_repository"),
            pagination_limit=source["pagination_limit"],
            maximum_response_bytes=source["maximum_response_bytes"],
            checksum_policy=source["checksum_policy"],
            eligible_adapters=tuple(source["eligible_adapters"]),
            eligible_configurations=tuple(source["eligible_configurations"]),
        )
        for source in document["release_sources"]
    )
    targets: list[ProductTarget] = []
    for target in document["targets"]:
        runner_data = target["runner"]
        runner = RunnerRequirements(
            runner_class=runner_data["class"],
            minimum_memory_mib=runner_data["minimum_memory_mib"],
            minimum_disk_mib=runner_data["minimum_disk_mib"],
            service_limits=tuple(ServiceLimit(**item) for item in runner_data["service_limits"]),
            larger_runner_variable=runner_data.get("larger_runner_variable"),
        )
        baselines = tuple(
            AcceptedBaseline(
                id=baseline["id"],
                released_version=baseline["released_version"],
                artifact_reference=baseline["artifact_reference"],
                artifact_identity=baseline["artifact_identity"],
                reports=dict(baseline["reports"]),
                primary_outcomes=tuple(baseline["primary_outcomes"]),
                comparison_policy=baseline["comparison_policy"],
                accepted_by_pr=baseline["accepted_by_pr"],
                canary=CanaryPolicy(**baseline["canary"]),
            )
            for baseline in target["accepted_baselines"]
        )
        targets.append(
            ProductTarget(
                id=target["id"],
                project=target["project"],
                project_url=target["project_url"],
                license=target["license"],
                artifact_kind=target["artifact_kind"],
                adapter=target["adapter"],
                release_source=target["release_source"],
                configuration_id=target["configuration_id"],
                configuration_path=target["configuration_path"],
                architecture=target["architecture"],
                duty=target["duty"],
                model=target["model"],
                profiles=tuple(target["profiles"]),
                dispatchable=target["dispatchable"],
                runner=runner,
                accepted_baselines=baselines,
            )
        )
    return ProductCatalog(schema=document["schema"], release_sources=release_sources, targets=tuple(targets))


def load_catalog(
    path: Path | str,
    *,
    repo_root: Path | str,
    adapter_names: Iterable[str],
    allowed_baseline_roots: Iterable[Path | str],
) -> ProductCatalog:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot load catalog: {exc}") from exc
    if not isinstance(document, dict):
        raise CatalogError("catalog root must be an object")
    _schema_error(document)
    root = Path(repo_root).resolve(strict=True)
    _validate_semantics(
        document,
        repo_root=root,
        adapter_names=set(adapter_names),
        allowed_baseline_roots=tuple(Path(value) for value in allowed_baseline_roots),
    )
    return _make_catalog(document)


def validate_reproduction_request(catalog: ProductCatalog, target_id: str, baseline_id: str) -> AcceptedBaseline:
    target = catalog.target(target_id)
    baseline = target.baseline(baseline_id)
    if baseline is None:
        raise CatalogError(f"reproduce mode requires an accepted baseline for {target_id}: {baseline_id}")
    return baseline


def validate_dispatch_choices(catalog: ProductCatalog, choices: Iterable[str]) -> None:
    actual = tuple(choices)
    expected = catalog.dispatchable_ids
    if actual != expected:
        raise CatalogError(f"workflow dispatch choices {actual!r} do not equal catalog choices {expected!r}")
