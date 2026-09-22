from __future__ import annotations

import copy
import hashlib
import json
import re
import stat
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal

from .comparison import canonical_json_bytes, sha256_bytes
from .paths import validate_fresh_output_path
from .results import ExperimentHealth
from .sanitizer import SensitiveValue, build_sanitizer
from .schemas import schema_validator

MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_MEMBERS = 500
MANIFEST_PATH = "bundle-manifest.json"
CHECKSUMS_PATH = "SHA256SUMS"
CONTROL_PATHS = frozenset({MANIFEST_PATH, CHECKSUMS_PATH})
MEMBER_PATTERN = re.compile(r"^(?!/)(?![A-Za-z]:)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]+$")
REQUIRED_REPORT_KEYS = frozenset({"control", "operator", "operator_raw", "response_midpoint", "comparison"})
EXPECTED_REPORT_PATHS = {
    "control": "reports/control.raw.json",
    "operator": "reports/operator.current.json",
    "operator_raw": "reports/operator.current.raw.json",
    "response_midpoint": "reports/response-midpoint.json",
    "comparison": "reports/comparison.json",
}
OPTIONAL_STATIC_MEMBERS = frozenset({"logs/target.sanitized.log"})
RENDERED_CONFIG_PATTERN = re.compile(
    r"^configuration/rendered-config\.[A-Za-z0-9][A-Za-z0-9._-]*$"
)
REQUIRED_STATIC_MEMBERS = frozenset(
    {
        "README.md",
        "badge/pii-leak-badge.json",
        "submission/submission.json",
        "submission/submission.md",
        "submission/submission-url.txt",
        "provenance/runner.json",
        "provenance/target-artifacts.json",
        "provenance/environment.json",
        "provenance/commands.txt",
        "provenance/dependency-inventory.txt",
        "provenance/workflow.json",
        "configuration/environment-allowlist.json",
        "logs/orchestration.log",
        "logs/readiness.log",
    }
)


class BundleError(ValueError):
    """A candidate evidence bundle is unsafe, incomplete, or internally inconsistent."""


class IncompleteEvidenceError(BundleError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.health = ExperimentHealth.NOT_MEASURED


def _reject_json_constant(value: str) -> None:
    raise ValueError(value)


@dataclass(frozen=True)
class BundleContent:
    path: str
    data: bytes = field(repr=False)
    kind: Literal["json", "text"]

    @classmethod
    def json(cls, path: str, document: Any) -> BundleContent:
        return cls(path=path, data=canonical_json_bytes(document), kind="json")

    @classmethod
    def text(cls, path: str, text: str) -> BundleContent:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"
        return cls(path=path, data=normalized.encode("utf-8"), kind="text")

    @classmethod
    def from_path(cls, path: str, source: Path) -> BundleContent:
        raw = source.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BundleError(f"bundle source is not UTF-8 text: {path}") from exc
        if path.endswith(".json"):
            return cls.json(path, _strict_json_loads(raw, path=path))
        return cls.text(path, text)


@dataclass(frozen=True)
class VerifiedBundle:
    manifest: Mapping[str, Any]
    member_sha256: Mapping[str, str]
    total_size: int


@dataclass(frozen=True)
class BundleBuildResult:
    bundle_dir: Path
    archive_path: Path
    archive_sha256: str
    manifest_sha256: str
    verified: VerifiedBundle


def _normalize_member_path(value: str) -> str:
    if not isinstance(value, str) or not value or not MEMBER_PATTERN.fullmatch(value):
        raise BundleError("bundle member path is unsafe")
    path = PurePosixPath(value)
    if value != path.as_posix() or any(part in ("", ".", "..") for part in path.parts):
        raise BundleError("bundle member path is not normalized")
    return value


def _path_identity(value: str) -> str:
    return _normalize_member_path(value).casefold()


def _validate_unique_paths(paths: Iterable[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    identities: set[str] = set()
    for path in paths:
        normalized = _normalize_member_path(path)
        identity = _path_identity(normalized)
        if identity in identities:
            raise BundleError("bundle contains duplicate normalized member paths")
        identities.add(identity)
        ordered.append(normalized)
    return tuple(ordered)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reject_private_artifacts(paths: Iterable[str]) -> None:
    for path in paths:
        lowered = path.casefold()
        if lowered.startswith("logs/") and (".raw." in lowered or lowered.endswith("raw.log")):
            raise BundleError("raw target logs cannot enter the evidence bundle")
        if "sanitizer" in lowered and ("dictionary" in lowered or "fixture" in lowered):
            raise BundleError("sanitizer dictionaries cannot enter the evidence bundle")


def _scan_sensitive(files: Mapping[str, bytes], sensitive_values: Sequence[SensitiveValue]) -> None:
    sanitizer = build_sanitizer(tuple(sensitive_values))
    for path, data in files.items():
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BundleError(f"bundle member is not UTF-8 text: {path}") from exc
        if sanitizer.contains_sensitive(text):
            raise BundleError(f"bundle member contains a sensitive fixture or credential: {path}")


def _required_member_paths(manifest: Mapping[str, Any]) -> set[str]:
    reports = manifest.get("reports")
    if not isinstance(reports, dict) or set(reports) != REQUIRED_REPORT_KEYS:
        raise IncompleteEvidenceError("bundle does not declare the complete required report set")
    if reports != EXPECTED_REPORT_PATHS:
        raise IncompleteEvidenceError("bundle report paths do not use the canonical layout")
    return set(REQUIRED_STATIC_MEMBERS) | set(EXPECTED_REPORT_PATHS.values())


def _validate_semantic_members(manifest: Mapping[str, Any], payload_paths: set[str]) -> None:
    missing = sorted(_required_member_paths(manifest) - payload_paths)
    rendered_configs = {path for path in payload_paths if RENDERED_CONFIG_PATTERN.fullmatch(path)}
    if not rendered_configs:
        missing.append("configuration/rendered-config.<extension>")
    if missing:
        raise IncompleteEvidenceError("bundle is missing required evidence: " + ", ".join(missing))
    if len(rendered_configs) != 1:
        raise BundleError("bundle must contain exactly one canonical rendered configuration")
    allowed = _required_member_paths(manifest) | OPTIONAL_STATIC_MEMBERS | rendered_configs
    unexpected = sorted(payload_paths - allowed)
    if unexpected:
        raise BundleError("bundle contains undeclared evidence roles: " + ", ".join(unexpected))


def _strict_json_loads(data: bytes, *, path: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise BundleError(f"bundle JSON member is invalid: {path}") from exc


def _validate_embedded_documents(files: Mapping[str, bytes], manifest: Mapping[str, Any]) -> None:
    reports = manifest.get("reports")
    if not isinstance(reports, dict) or not isinstance(reports.get("comparison"), str):
        raise IncompleteEvidenceError("bundle does not declare a comparison report")
    documents: dict[str, Any] = {}
    for path, schema_name in (
        (reports["comparison"], "comparison"),
        ("submission/submission.json", "submission"),
    ):
        try:
            data = files[path]
        except KeyError as exc:
            raise BundleError(f"required embedded document is invalid: {path}") from exc
        document = _strict_json_loads(data, path=path)
        if path == "submission/submission.json" and "bundle_manifest_sha256" in document:
            raise BundleError("embedded submission cannot contain the final manifest hash")
        errors = sorted(schema_validator(schema_name).iter_errors(document), key=lambda error: list(error.path))
        if errors:
            raise BundleError(f"embedded document violates {schema_name} schema: {path}")
        documents[schema_name] = document
    _validate_cross_document_consistency(manifest, documents["comparison"], documents["submission"], files)


def _validate_cross_document_consistency(
    manifest: Mapping[str, Any],
    comparison: Mapping[str, Any],
    submission: Mapping[str, Any],
    files: Mapping[str, bytes],
) -> None:
    product = manifest["product"]
    expected_submission = {
        "gateway": product["project"],
        "requested_release_selector": product["requested_release_selector"],
        "version": product["released_version"],
        "artifact_identity": product["artifact_identity"],
        "release_url": product["release_url"],
        "configuration_id": product["configuration_id"],
        "configuration_sha256": product["configuration_sha256"],
        "project_url": product["project_url"],
        "run_url": (
            f"https://github.com/{manifest['workflow']['repository']}"
            f"/actions/runs/{manifest['workflow']['run_id']}"
        ),
        "license": product["license"],
        "mode": manifest["mode"],
    }
    for field_name, expected in expected_submission.items():
        if submission.get(field_name) != expected:
            raise BundleError(f"submission metadata disagrees with manifest: {field_name}")

    manifest_reports = manifest["reports"]
    expected_report_paths = {
        key: manifest_reports[key]
        for key in ("operator", "operator_raw", "response_midpoint", "comparison")
    }
    if submission.get("report_paths") != expected_report_paths:
        raise BundleError("submission report paths disagree with manifest")
    if comparison.get("target_id") != manifest["target_id"]:
        raise BundleError("comparison target disagrees with manifest")
    if comparison.get("mode") != manifest["mode"]:
        raise BundleError("comparison mode disagrees with manifest")

    operator_report = _strict_json_loads(files[manifest_reports["operator"]], path=manifest_reports["operator"])
    response_report = _strict_json_loads(
        files[manifest_reports["response_midpoint"]],
        path=manifest_reports["response_midpoint"],
    )
    if not isinstance(operator_report, dict) or not isinstance(response_report, dict):
        raise BundleError("product reports must be JSON objects")
    operator_result = operator_report.get("verdict")
    response_result = {
        "pass": "CLEAN",
        "fail": "LEAK",
        "no-leak-profile-not-met": "CHECK FAILED",
    }.get(response_report.get("outcome"), "NOT MEASURED")
    expected_results = manifest["product_results"]
    if operator_result != expected_results["operator"]:
        raise BundleError("operator product result disagrees with its report")
    if response_result != expected_results["response_midpoint"]:
        raise BundleError("response product result disagrees with its report")

    baseline = manifest.get("baseline")
    if manifest["mode"] == "reproduce":
        if not isinstance(baseline, dict) or comparison.get("baseline_id") != baseline.get("id"):
            raise BundleError("comparison baseline disagrees with manifest")
        if submission.get("baseline") != {"id": baseline["id"], "status": "accepted"}:
            raise BundleError("submission baseline disagrees with manifest")
    elif "baseline" in submission or baseline is not None:
        raise BundleError("measure bundle cannot claim a baseline")

    comparison_status = comparison["profiles"]
    expected_status = manifest["reproduction_status"]
    if comparison_status["operator"]["status"] != expected_status["operator"]:
        raise BundleError("operator comparison status disagrees with manifest")
    if comparison_status["response-midpoint"]["status"] != expected_status["response_midpoint"]:
        raise BundleError("response comparison status disagrees with manifest")

    baseline_reports = baseline.get("reports", {}) if isinstance(baseline, dict) else {}
    for profile, report_key, baseline_key in (
        ("operator", "operator", "operator"),
        ("response-midpoint", "response_midpoint", "response_midpoint"),
    ):
        profile_comparison = comparison_status[profile]
        current_path = manifest_reports[report_key]
        if profile_comparison.get("current_sha256") != _sha256(files[current_path]):
            raise BundleError(f"{profile} current report hash disagrees with bundle")
        declared_baseline = baseline_reports.get(baseline_key)
        if declared_baseline is None:
            if profile_comparison["status"] != "not-compared" or "baseline_sha256" in profile_comparison:
                raise BundleError(f"{profile} comparison claims an undeclared baseline")
        elif profile_comparison.get("baseline_sha256") != declared_baseline.get("sha256"):
            raise BundleError(f"{profile} baseline hash disagrees with manifest")


def _checksum_bytes(files: Mapping[str, bytes]) -> bytes:
    return "".join(f"{_sha256(files[path])}  {path}\n" for path in sorted(files)).encode("utf-8")


def _write_deterministic_zip(archive_path: Path, files: Mapping[str, bytes]) -> None:
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, files[path])


def build_bundle(
    *,
    repo_root: Path,
    bundle_dir: Path,
    archive_path: Path,
    manifest_template: Mapping[str, Any],
    members: Sequence[BundleContent],
    sensitive_values: Sequence[SensitiveValue],
    frozen_roots: Iterable[Path] = (),
) -> BundleBuildResult:
    destination = validate_fresh_output_path(bundle_dir, repo_root=repo_root, frozen_roots=frozen_roots)
    archive = validate_fresh_output_path(archive_path, repo_root=repo_root, frozen_roots=frozen_roots)
    if destination == archive or destination in archive.parents or archive in destination.parents:
        raise BundleError("bundle directory and archive path must be disjoint")
    if "members" in manifest_template:
        raise BundleError("manifest template cannot predeclare member hashes")
    if len(members) > MAX_MEMBERS - len(CONTROL_PATHS):
        raise BundleError("bundle has too many members")

    paths = _validate_unique_paths(member.path for member in members)
    if any(path in CONTROL_PATHS for path in paths):
        raise BundleError("bundle payload cannot replace control files")
    _reject_private_artifacts(paths)
    files: dict[str, bytes] = {}
    for member, path in zip(members, paths, strict=True):
        if len(member.data) > MAX_MEMBER_BYTES:
            raise BundleError(f"bundle member exceeds size limit: {path}")
        if member.kind == "json":
            canonical = canonical_json_bytes(_strict_json_loads(member.data, path=path))
            if canonical != member.data:
                raise BundleError(f"bundle JSON member is not canonical: {path}")
        elif member.kind == "text":
            try:
                text = member.data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BundleError(f"bundle text member is not UTF-8: {path}") from exc
            if BundleContent.text(path, text).data != member.data:
                raise BundleError(f"bundle text member does not use canonical line endings: {path}")
        else:
            raise BundleError(f"bundle member has an unsupported content kind: {path}")
        files[path] = member.data

    payload_paths = set(files)
    manifest = copy.deepcopy(dict(manifest_template))
    _validate_semantic_members(manifest, payload_paths)
    manifest["members"] = [
        {"path": path, "sha256": _sha256(files[path]), "size": len(files[path])}
        for path in sorted(files)
    ]
    errors = sorted(schema_validator("bundle-manifest").iter_errors(manifest), key=lambda error: list(error.path))
    if errors:
        raise BundleError(f"bundle manifest violates schema at {list(errors[0].path)!r}")
    _validate_embedded_documents(files, manifest)
    _scan_sensitive(files, sensitive_values)
    manifest_bytes = canonical_json_bytes(manifest)
    if build_sanitizer(tuple(sensitive_values)).contains_sensitive(manifest_bytes.decode("utf-8")):
        raise BundleError("bundle manifest contains a sensitive fixture or credential")

    complete_files = dict(files)
    complete_files[MANIFEST_PATH] = manifest_bytes
    complete_files[CHECKSUMS_PATH] = _checksum_bytes(complete_files)
    if sum(len(data) for data in complete_files.values()) > MAX_ARTIFACT_BYTES:
        raise BundleError("bundle exceeds total size limit")

    destination.mkdir(mode=0o700, parents=True)
    for path in sorted(complete_files):
        target = destination.joinpath(*PurePosixPath(path).parts)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(complete_files[path])
    archive.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_deterministic_zip(archive, complete_files)
    verified = verify_bundle(archive, sensitive_values=sensitive_values)
    return BundleBuildResult(
        bundle_dir=destination,
        archive_path=archive,
        archive_sha256=sha256_bytes(archive.read_bytes()),
        manifest_sha256=sha256_bytes(manifest_bytes),
        verified=verified,
    )


def _read_zip(path: Path) -> dict[str, bytes]:
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise BundleError("bundle archive exceeds size limit")
    files: dict[str, bytes] = {}
    total = 0
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS:
                raise BundleError("bundle archive has too many members")
            if archive.comment:
                raise BundleError("bundle archive comment is not canonical")
            names = _validate_unique_paths(info.filename for info in infos)
            if list(names) != sorted(names):
                raise BundleError("bundle archive member order is not canonical")
            for info, name in zip(infos, names, strict=True):
                if info.is_dir():
                    raise BundleError("bundle archive cannot contain directory entries")
                if (
                    info.compress_type != zipfile.ZIP_STORED
                    or info.date_time != (1980, 1, 1, 0, 0, 0)
                    or info.create_system != 3
                    or info.extra
                    or info.comment
                    or info.flag_bits & 0x1
                ):
                    raise BundleError(f"bundle archive member metadata is not canonical: {name}")
                unix_mode = (info.external_attr >> 16) & 0o177777
                if stat.S_ISLNK(unix_mode):
                    raise BundleError("bundle archive contains a symbolic link")
                if unix_mode != 0o100644:
                    raise BundleError(f"bundle archive member mode is not canonical: {name}")
                if info.file_size > MAX_MEMBER_BYTES:
                    raise BundleError(f"bundle archive member exceeds size limit: {name}")
                total += info.file_size
                if total > MAX_ARTIFACT_BYTES:
                    raise BundleError("bundle archive expands beyond its total size limit")
                files[name] = archive.read(info)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise BundleError("bundle archive cannot be read") from exc
    return files


def _read_directory(path: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    total = 0
    for candidate in sorted(path.rglob("*")):
        if candidate.is_symlink():
            raise BundleError("bundle directory contains a symbolic link")
        if not candidate.is_file():
            continue
        if len(files) >= MAX_MEMBERS:
            raise BundleError("bundle directory has too many members")
        relative = candidate.relative_to(path).as_posix()
        _normalize_member_path(relative)
        size = candidate.stat().st_size
        if size > MAX_MEMBER_BYTES:
            raise BundleError(f"bundle directory member exceeds size limit: {relative}")
        total += size
        if total > MAX_ARTIFACT_BYTES:
            raise BundleError("bundle directory expands beyond its total size limit")
        files[relative] = candidate.read_bytes()
    return files


def verify_bundle(
    source: Path,
    *,
    sensitive_values: Sequence[SensitiveValue] = (),
) -> VerifiedBundle:
    resolved = source.resolve(strict=True)
    files = _read_directory(resolved) if resolved.is_dir() else _read_zip(resolved)
    _validate_unique_paths(files)
    _reject_private_artifacts(files)
    if not CONTROL_PATHS.issubset(files):
        raise BundleError("bundle is missing its manifest or checksum file")
    if sum(len(data) for data in files.values()) > MAX_ARTIFACT_BYTES:
        raise BundleError("bundle expands beyond its total size limit")

    try:
        manifest = _strict_json_loads(files[MANIFEST_PATH], path=MANIFEST_PATH)
    except BundleError as exc:
        raise BundleError("bundle manifest is not valid UTF-8 JSON") from exc
    if canonical_json_bytes(manifest) != files[MANIFEST_PATH]:
        raise BundleError("bundle manifest is not canonical JSON")
    errors = sorted(schema_validator("bundle-manifest").iter_errors(manifest), key=lambda error: list(error.path))
    if errors:
        raise BundleError(f"bundle manifest violates schema at {list(errors[0].path)!r}")

    declared_members = manifest["members"]
    declared_paths = _validate_unique_paths(item["path"] for item in declared_members)
    payload_paths = set(files) - CONTROL_PATHS
    if set(declared_paths) != payload_paths:
        raise BundleError("manifest member inventory does not exactly cover bundle payload")
    for item in declared_members:
        path = item["path"]
        data = files[path]
        if len(data) != item["size"] or _sha256(data) != item["sha256"]:
            raise BundleError(f"manifest member hash or size mismatch: {path}")
    _validate_semantic_members(manifest, payload_paths)
    _validate_embedded_documents(files, manifest)

    for path in payload_paths:
        if path.endswith(".json"):
            if canonical_json_bytes(_strict_json_loads(files[path], path=path)) != files[path]:
                raise BundleError(f"bundle JSON member is not canonical: {path}")
        else:
            try:
                text = files[path].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BundleError(f"bundle text member is not UTF-8: {path}") from exc
            if BundleContent.text(path, text).data != files[path]:
                raise BundleError(f"bundle text member does not use canonical line endings: {path}")

    checksum_inputs = {path: data for path, data in files.items() if path != CHECKSUMS_PATH}
    if files[CHECKSUMS_PATH] != _checksum_bytes(checksum_inputs):
        raise BundleError("SHA256SUMS is incomplete, noncanonical, or incorrect")
    _scan_sensitive(files, tuple(sensitive_values))
    return VerifiedBundle(
        manifest=MappingProxyType(copy.deepcopy(manifest)),
        member_sha256=MappingProxyType({path: _sha256(files[path]) for path in sorted(payload_paths)}),
        total_size=sum(len(data) for data in files.values()),
    )
