from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .release import (
    ReleaseHttpResponse,
    ResolvedRelease,
    _artifact_url,
    _retry_after_seconds,
    fetch_release_artifact,
)
from .retry import RetryableAcquisitionError

MAX_WHEEL_BYTES = 64 * 1024 * 1024
WHEEL_FILENAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\.whl$")
SHA256_IDENTITY = re.compile(r"^sha256:[a-f0-9]{64}$")
FetchArtifact = Callable[[str, int], ReleaseHttpResponse]


class WheelAcquisitionError(ValueError):
    """The downloaded bytes cannot be attributed to the resolved release."""


@dataclass(frozen=True)
class VerifiedWheel:
    path: Path
    sha256: str
    size: int
    source_url: str


def acquire_release_wheel(
    release: ResolvedRelease,
    destination_dir: Path,
    *,
    fetch: FetchArtifact = fetch_release_artifact,
) -> VerifiedWheel:
    """Save a wheel only after its source, size, and digest match release metadata."""
    filename = release.artifact_reference
    if not WHEEL_FILENAME.fullmatch(filename) or ".." in filename:
        raise WheelAcquisitionError("resolved wheel filename is unsafe")
    if release.media_type != "application/zip" or release.release_state != "stable" or release.yanked:
        raise WheelAcquisitionError("resolved release is not an eligible stable wheel")
    if not SHA256_IDENTITY.fullmatch(release.artifact_identity):
        raise WheelAcquisitionError("resolved wheel has no valid SHA-256 identity")
    if not isinstance(release.artifact_size, int) or isinstance(release.artifact_size, bool):
        raise WheelAcquisitionError("resolved wheel size is invalid")
    if release.artifact_size <= 0 or release.artifact_size > MAX_WHEEL_BYTES:
        raise WheelAcquisitionError("resolved wheel exceeds acquisition size limit")
    _artifact_url(release.artifact_url)
    if not destination_dir.is_dir() or destination_dir.is_symlink():
        raise WheelAcquisitionError("wheel destination must be a real existing directory")
    destination = destination_dir / filename
    if destination.exists() or destination.is_symlink():
        raise WheelAcquisitionError("wheel destination already exists")

    response = fetch(release.artifact_url, release.artifact_size)
    if response.status_code != 200:
        if response.status_code == 429 or 500 <= response.status_code < 600:
            retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
            raise RetryableAcquisitionError(
                "release artifact request failed",
                category="release-artifact-http",
                retry_after_seconds=_retry_after_seconds(retry_after),
            )
        raise WheelAcquisitionError(f"release artifact request returned HTTP {response.status_code}")
    declared = response.headers.get("Content-Length") or response.headers.get("content-length")
    if declared is not None:
        try:
            declared_size = int(declared)
        except ValueError as exc:
            raise WheelAcquisitionError("release artifact has invalid Content-Length") from exc
        if declared_size != release.artifact_size:
            raise WheelAcquisitionError("release artifact Content-Length does not match metadata")
    payload = response.payload
    if len(payload) != release.artifact_size:
        raise WheelAcquisitionError("release artifact byte count does not match metadata")
    digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    if digest != release.artifact_identity:
        raise WheelAcquisitionError("release artifact SHA-256 does not match metadata")
    try:
        with destination.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise WheelAcquisitionError("wheel destination already exists") from exc
    except OSError:
        destination.unlink(missing_ok=True)
        raise
    return VerifiedWheel(path=destination, sha256=digest, size=len(payload), source_url=release.artifact_url)
