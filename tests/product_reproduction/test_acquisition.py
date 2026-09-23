from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from benchmarks.product_reproduction import release as release_module
from benchmarks.product_reproduction.acquisition import WheelAcquisitionError, acquire_release_wheel
from benchmarks.product_reproduction.release import (
    ReleaseHttpResponse,
    ReleaseResolutionError,
    ResolvedRelease,
    fetch_release_artifact,
)
from benchmarks.product_reproduction.retry import RetryableAcquisitionError


def _release(payload: bytes) -> ResolvedRelease:
    digest = hashlib.sha256(payload).hexdigest()
    return ResolvedRelease(
        version="1.6.6",
        release_url="https://pypi.org/project/llm-shield-proxy/1.6.6/",
        release_state="stable",
        yanked=False,
        artifact_reference="llm_shield_proxy-1.6.6-py3-none-any.whl",
        artifact_url="https://files.pythonhosted.org/packages/llm_shield_proxy-1.6.6-py3-none-any.whl",
        artifact_identity=f"sha256:{digest}",
        artifact_size=len(payload),
        media_type="application/zip",
    )


def test_acquire_release_wheel_keeps_only_verified_bytes(tmp_path: Path) -> None:
    payload = b"wheel bytes from the reviewed release"
    release = _release(payload)
    destination = tmp_path / "wheels"
    destination.mkdir()
    observed: list[tuple[str, int]] = []

    def fetch(url: str, maximum_bytes: int) -> ReleaseHttpResponse:
        observed.append((url, maximum_bytes))
        return ReleaseHttpResponse(200, {"Content-Length": str(len(payload))}, payload)

    wheel = acquire_release_wheel(release, destination, fetch=fetch)

    assert wheel.path == destination / release.artifact_reference
    assert wheel.path.read_bytes() == payload
    assert wheel.sha256 == release.artifact_identity
    assert observed == [(release.artifact_url, len(payload))]


@pytest.mark.parametrize("mutation", ["digest", "size", "content-length", "redirect", "oversize"])
def test_acquisition_rejects_unverified_wheel_without_writing(
    tmp_path: Path, mutation: str
) -> None:
    payload = b"verified wheel bytes"
    release = _release(payload)
    if mutation == "digest":
        release = ResolvedRelease(**{**release.__dict__, "artifact_identity": "sha256:" + "0" * 64})
    elif mutation == "size":
        release = ResolvedRelease(**{**release.__dict__, "artifact_size": len(payload) + 1})
    elif mutation == "oversize":
        release = ResolvedRelease(**{**release.__dict__, "artifact_size": 65 * 1024 * 1024})
    response = ReleaseHttpResponse(
        302 if mutation == "redirect" else 200,
        {"Content-Length": str(len(payload) + (1 if mutation == "content-length" else 0))},
        payload,
    )
    destination = tmp_path / "wheels"
    destination.mkdir()

    with pytest.raises(WheelAcquisitionError):
        acquire_release_wheel(release, destination, fetch=lambda *_: response)

    assert not list(destination.iterdir())


def test_acquisition_retries_transient_http_without_writing(tmp_path: Path) -> None:
    payload = b"verified wheel bytes"
    destination = tmp_path / "wheels"
    destination.mkdir()

    with pytest.raises(RetryableAcquisitionError) as caught:
        acquire_release_wheel(
            _release(payload),
            destination,
            fetch=lambda *_: ReleaseHttpResponse(429, {"Retry-After": "2"}, b""),
        )

    assert caught.value.retry_after_seconds == 2
    assert not list(destination.iterdir())


def test_acquisition_will_not_replace_an_existing_wheel(tmp_path: Path) -> None:
    payload = b"verified wheel bytes"
    destination = tmp_path / "wheels"
    destination.mkdir()
    existing = destination / _release(payload).artifact_reference
    existing.write_bytes(b"preexisting user bytes")

    with pytest.raises(WheelAcquisitionError, match="already exists"):
        acquire_release_wheel(
            _release(payload),
            destination,
            fetch=lambda *_: ReleaseHttpResponse(200, {}, payload),
        )

    assert existing.read_bytes() == b"preexisting user bytes"


def test_artifact_fetch_connects_to_pinned_public_ip_with_original_tls_name(monkeypatch) -> None:
    observed = []
    monkeypatch.setattr(release_module, "_resolve_public_addresses", lambda *_: ("151.101.0.223",))

    def send(target, maximum_bytes, *, accept):
        observed.append((target, maximum_bytes, accept))
        return ReleaseHttpResponse(200, {}, b"wheel")

    monkeypatch.setattr(release_module, "_pinned_https_get", send)
    url = "https://files.pythonhosted.org/packages/reviewed.whl"
    assert fetch_release_artifact(url, 123).payload == b"wheel"
    target, maximum_bytes, accept = observed[0]
    assert target.url == "https://151.101.0.223/packages/reviewed.whl"
    assert target.headers == {"Host": "files.pythonhosted.org"}
    assert target.extensions == {"sni_hostname": "files.pythonhosted.org"}
    assert (maximum_bytes, accept) == (123, "application/octet-stream")


@pytest.mark.parametrize(
    "url",
    [
        "https://files.pythonhosted.org:444/packages/reviewed.whl",
        "https://elsewhere.example/packages/reviewed.whl",
        "https://files.pythonhosted.org/packages/reviewed.whl?redirect=1",
    ],
)
def test_artifact_fetch_rejects_non_allowlisted_urls(url: str) -> None:
    with pytest.raises(ReleaseResolutionError):
        fetch_release_artifact(url, 123)
