from __future__ import annotations

import ipaddress
import json
import re
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from .catalog import AcceptedBaseline, ProductCatalog, ProductTarget, ReleaseSource
from .retry import RetryableAcquisitionError

MAX_JSON_DEPTH = 128
PYPI_ARTIFACT_HOST = "files.pythonhosted.org"
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
RunMode = Literal["measure", "reproduce"]
RequestedMode = Literal["auto", "measure", "reproduce"]
FetchJson = Callable[[str, int], Mapping[str, Any]]


class ReleaseResolutionError(ValueError):
    """Release metadata cannot prove one eligible immutable artifact."""


@dataclass(frozen=True)
class RunSelection:
    target: ProductTarget
    source: ReleaseSource
    release_selector: str
    mode: RunMode
    version: str | None
    expected_reference: str | None
    expected_identity: str | None
    baseline: AcceptedBaseline | None


@dataclass(frozen=True)
class ResolvedRelease:
    version: str
    release_url: str
    release_state: Literal["stable"]
    yanked: bool
    artifact_reference: str
    artifact_url: str
    artifact_identity: str
    artifact_size: int
    media_type: str


@dataclass(frozen=True)
class PinnedReleaseTarget:
    url: str
    headers: Mapping[str, str]
    extensions: Mapping[str, str]


@dataclass(frozen=True)
class ReleaseHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    payload: bytes


def _source(catalog: ProductCatalog, source_id: str) -> ReleaseSource:
    for source in catalog.release_sources:
        if source.id == source_id:
            return source
    raise ReleaseResolutionError(f"catalog target names unknown release source: {source_id}")


def select_run(
    catalog: ProductCatalog,
    *,
    target_id: str,
    release_selector: str,
    requested_mode: RequestedMode,
) -> RunSelection:
    if requested_mode not in ("auto", "measure", "reproduce"):
        raise ReleaseResolutionError(f"unknown mode: {requested_mode}")
    target = catalog.target(target_id)
    source = _source(catalog, target.release_source)
    if release_selector == "latest-release":
        if requested_mode == "reproduce":
            raise ReleaseResolutionError("invalid mode: latest-release can only be measured")
        return RunSelection(
            target=target,
            source=source,
            release_selector=release_selector,
            mode="measure",
            version=None,
            expected_reference=None,
            expected_identity=None,
            baseline=None,
        )

    baseline = target.baseline(release_selector)
    if baseline is None:
        raise ReleaseResolutionError(
            f"release selector is not latest-release or an accepted baseline for {target_id}"
        )
    if requested_mode == "measure":
        raise ReleaseResolutionError("invalid mode: an accepted baseline must use reproduce")
    return RunSelection(
        target=target,
        source=source,
        release_selector=release_selector,
        mode="reproduce",
        version=baseline.released_version,
        expected_reference=baseline.artifact_reference,
        expected_identity=baseline.artifact_identity,
        baseline=baseline,
    )


def _bounded_json_depth(value: object) -> None:
    pending: list[tuple[object, int]] = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise ReleaseResolutionError("release metadata exceeds maximum JSON depth")
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)


def _metadata_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "pypi.org"
        or parsed.netloc != "pypi.org"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ReleaseResolutionError("release metadata URL left the allowlisted PyPI origin")


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def _resolve_public_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        answers = socket.getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as exc:
        raise RetryableAcquisitionError(
            "release metadata DNS resolution failed",
            category="release-metadata-dns",
        ) from exc

    addresses: list[str] = []
    for answer in answers:
        candidate = answer[4][0]
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError as exc:
            raise ReleaseResolutionError("release metadata DNS returned an invalid address") from exc
        if not address.is_global:
            raise ReleaseResolutionError("release metadata DNS returned a non-public address")
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise RetryableAcquisitionError(
            "release metadata DNS returned no addresses",
            category="release-metadata-dns",
        )
    return tuple(addresses)


def _pin_release_url(url: str) -> PinnedReleaseTarget:
    parsed = urlsplit(url)
    host = parsed.hostname
    if host is None:
        raise ReleaseResolutionError("release metadata URL has no host")
    addresses = _resolve_public_addresses(host, 443)
    address = ipaddress.ip_address(addresses[0])
    if not address.is_global:
        raise ReleaseResolutionError("release metadata DNS returned a non-public address")
    pinned_host = f"[{address}]" if address.version == 6 else str(address)
    pinned_url = urlunsplit((parsed.scheme, pinned_host, parsed.path, parsed.query, parsed.fragment))
    return PinnedReleaseTarget(
        url=pinned_url,
        headers={"Host": host},
        extensions={"sni_hostname": host},
    )


def _pinned_https_get(target: PinnedReleaseTarget, maximum_bytes: int) -> ReleaseHttpResponse:
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": "pii-leak-benchmark-product-reproduction/1",
        **target.headers,
    }
    try:
        with httpx.Client(follow_redirects=False, trust_env=False, timeout=20.0) as client:
            request = client.build_request(
                "GET",
                target.url,
                headers=headers,
                extensions=dict(target.extensions),
            )
            response = client.send(request, stream=True)
            try:
                payload = bytearray()
                for chunk in response.iter_bytes():
                    remaining = maximum_bytes + 1 - len(payload)
                    if remaining <= 0:
                        break
                    payload.extend(chunk[:remaining])
                    if len(payload) > maximum_bytes:
                        break
                return ReleaseHttpResponse(
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    payload=bytes(payload),
                )
            finally:
                response.close()
    except httpx.TransportError as exc:
        raise RetryableAcquisitionError(
            "release metadata request failed",
            category="release-metadata-network",
        ) from exc


def fetch_release_json(url: str, maximum_bytes: int) -> Mapping[str, Any]:
    _metadata_url(url)
    target = _pin_release_url(url)
    response = _pinned_https_get(target, maximum_bytes)
    if response.status_code != 200:
        retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
        delay = _retry_after_seconds(retry_after)
        if response.status_code == 429 or 500 <= response.status_code < 600:
            raise RetryableAcquisitionError(
                "release metadata request failed",
                category="release-metadata-http",
                retry_after_seconds=delay,
            )
        raise ReleaseResolutionError(f"release metadata request returned HTTP {response.status_code}")
    declared = response.headers.get("Content-Length") or response.headers.get("content-length")
    if declared is not None:
        try:
            declared_size = int(declared)
        except ValueError as exc:
            raise ReleaseResolutionError("release metadata has invalid Content-Length") from exc
        if declared_size < 0 or declared_size > maximum_bytes:
            raise ReleaseResolutionError("release metadata exceeds maximum response size")
    payload = response.payload
    if len(payload) > maximum_bytes:
        raise ReleaseResolutionError("release metadata exceeds maximum response size")
    try:
        document = json.loads(
            payload,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ReleaseResolutionError("release metadata is not bounded valid JSON") from exc
    if not isinstance(document, dict):
        raise ReleaseResolutionError("release metadata root must be an object")
    _bounded_json_depth(document)
    return document


def _pypi_url(source: ReleaseSource, version: str | None) -> str:
    if source.package is None:
        raise ReleaseResolutionError("PyPI release source has no package")
    package = quote(source.package, safe=".-_")
    if version is None:
        return f"{source.api_base_url}/pypi/{package}/json"
    return f"{source.api_base_url}/pypi/{package}/{quote(version, safe='.-_')}/json"


def _stable_version(document: Mapping[str, Any], source: ReleaseSource, requested: str | None) -> str:
    info = document.get("info")
    version = info.get("version") if isinstance(info, dict) else None
    name = info.get("name") if isinstance(info, dict) else None
    if not isinstance(name, str) or source.package is None or name.lower() != source.package.lower():
        raise ReleaseResolutionError("release package name does not match reviewed source")
    if not isinstance(version, str) or not re.fullmatch(source.version_pattern, version):
        raise ReleaseResolutionError("release version is absent, prerelease, or ineligible")
    if requested is not None and version != requested:
        raise ReleaseResolutionError("release metadata version does not match requested version")
    return version


def _artifact_url(value: object) -> str:
    if not isinstance(value, str):
        raise ReleaseResolutionError("artifact URL is absent")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != PYPI_ARTIFACT_HOST
        or parsed.netloc != PYPI_ARTIFACT_HOST
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ReleaseResolutionError("artifact URL is not an allowlisted credential-free PyPI URL")
    return value


def _select_wheel(document: Mapping[str, Any], source: ReleaseSource) -> Mapping[str, Any]:
    urls = document.get("urls")
    if not isinstance(urls, list):
        raise ReleaseResolutionError("release metadata has no artifact list")
    eligible: list[Mapping[str, Any]] = []
    for item in urls:
        if not isinstance(item, dict) or item.get("packagetype") != "bdist_wheel":
            continue
        filename = item.get("filename")
        if isinstance(filename, str) and re.fullmatch(source.assets.name_pattern, filename):
            eligible.append(item)
    if len(eligible) != 1:
        raise ReleaseResolutionError("release must contain exactly one eligible wheel")
    return eligible[0]


def resolve_release(
    source: ReleaseSource,
    *,
    version: str | None,
    expected_reference: str | None = None,
    expected_identity: str | None = None,
    fetch_json: FetchJson = fetch_release_json,
) -> ResolvedRelease:
    if source.source_type != "pypi-json":
        raise ReleaseResolutionError(f"unsupported release source type: {source.source_type}")
    document = fetch_json(_pypi_url(source, version), source.maximum_response_bytes)
    resolved_version = _stable_version(document, source, version)
    wheel = _select_wheel(document, source)
    if wheel.get("yanked") is not False:
        raise ReleaseResolutionError("eligible wheel is yanked or has unknown yanked state")
    digest_block = wheel.get("digests")
    digest = digest_block.get("sha256") if isinstance(digest_block, dict) else None
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise ReleaseResolutionError("eligible wheel has no valid SHA-256")
    identity = f"sha256:{digest}"
    if expected_identity is not None and identity != expected_identity:
        raise ReleaseResolutionError("resolved artifact identity does not match accepted baseline")
    filename = wheel.get("filename")
    size = wheel.get("size")
    if not isinstance(filename, str) or not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ReleaseResolutionError("eligible wheel metadata is incomplete")
    filename_parts = filename.split("-")
    if len(filename_parts) < 5 or filename_parts[1] != resolved_version:
        raise ReleaseResolutionError("artifact filename version does not match resolved release version")
    if expected_reference is not None and filename != expected_reference:
        raise ReleaseResolutionError("resolved artifact reference does not match accepted baseline")
    if "application/zip" not in source.assets.media_types:
        raise ReleaseResolutionError("release source does not allow wheel media type")
    artifact_url = _artifact_url(wheel.get("url"))
    if PurePosixPath(urlsplit(artifact_url).path).name != filename:
        raise ReleaseResolutionError("artifact URL filename does not match metadata")
    return ResolvedRelease(
        version=resolved_version,
        release_url=f"https://pypi.org/project/{source.package}/{resolved_version}/",
        release_state="stable",
        yanked=False,
        artifact_reference=filename,
        artifact_url=artifact_url,
        artifact_identity=identity,
        artifact_size=size,
        media_type="application/zip",
    )
