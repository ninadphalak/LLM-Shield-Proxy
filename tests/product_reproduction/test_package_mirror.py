from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from urllib.parse import urlsplit

import httpx
import pytest

from benchmarks.product_reproduction.package_mirror import (
    PackageMirror,
    PackageMirrorError,
    _official_fetch,
    validate_wheel_metadata,
)
from benchmarks.product_reproduction.release import ReleaseHttpResponse, ReleaseResolutionError


def _wheel(*, direct_url: bool = False) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        requirement = "Requires-Dist: dep @ https://unreviewed.example/dep.whl\n" if direct_url else ""
        archive.writestr(
            "dependency-1.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: dependency\nVersion: 1.0\n" + requirement,
        )
    return output.getvalue()


def _fetcher(payload: bytes, *, file_url: str = "https://files.pythonhosted.org/packages/dependency-1.0-py3-none-any.whl"):
    digest = hashlib.sha256(payload).hexdigest()
    index = json.dumps({"files": [{
        "filename": "dependency-1.0-py3-none-any.whl",
        "hashes": {"sha256": digest},
        "url": file_url,
        "size": len(payload),
        "requires-python": ">=3.9",
        "yanked": False,
    }]}).encode()
    calls: list[str] = []

    def fetch(url: str, maximum_bytes: int, accept: str) -> ReleaseHttpResponse:
        calls.append(url)
        return ReleaseHttpResponse(200, {}, index if url.startswith("https://pypi.org/") else payload)

    return fetch, calls, digest


def _local_url(mirror: PackageMirror) -> str:
    return mirror.index_url.replace("host.docker.internal", "127.0.0.1")


def _file_url(mirror: PackageMirror, link: str) -> str:
    parsed = urlsplit(_local_url(mirror))
    return f"{parsed.scheme}://{parsed.netloc}{link}"


def test_mirror_serves_only_hash_verified_official_wheels() -> None:
    payload = _wheel()
    fetch, calls, digest = _fetcher(payload)
    with PackageMirror(fetch) as mirror:
        index = httpx.get(_local_url(mirror) + "dependency/", timeout=5)
        assert index.status_code == 200
        assert "data-requires-python" in index.text
        link = re.search(r'href="([^"]+)"', index.text)
        assert link is not None
        file_url = _file_url(mirror, link.group(1))
        wheel = httpx.get(file_url, timeout=5)
        assert wheel.status_code == 200
        assert wheel.content == payload
        assert digest in file_url
        unknown = httpx.get(_local_url(mirror).replace("/simple/", "/files/") + "x/unknown.whl", timeout=5)
        assert unknown.status_code == 502
    assert calls == [
        "https://pypi.org/simple/dependency/",
        "https://files.pythonhosted.org/packages/dependency-1.0-py3-none-any.whl",
    ]


def test_mirror_rejects_nonofficial_wheel_link() -> None:
    fetch, _, _ = _fetcher(_wheel(), file_url="https://unreviewed.example/dependency.whl")
    with PackageMirror(fetch) as mirror:
        response = httpx.get(_local_url(mirror) + "dependency/", timeout=5)
        assert response.status_code == 502
        assert mirror.errors


def test_mirror_rejects_official_index_redirect() -> None:
    def redirect(url: str, maximum_bytes: int, accept: str) -> ReleaseHttpResponse:
        return ReleaseHttpResponse(302, {"Location": "https://unreviewed.example/simple/dep/"}, b"")

    with PackageMirror(redirect) as mirror:
        response = httpx.get(_local_url(mirror) + "dependency/", timeout=5)
        assert response.status_code == 502
        assert mirror.errors


def test_mirror_rejects_wheel_bytes_that_miss_index_hash() -> None:
    payload = _wheel()
    fetch, _, _ = _fetcher(payload)

    def changed(url: str, maximum_bytes: int, accept: str) -> ReleaseHttpResponse:
        if url.startswith("https://files.pythonhosted.org/"):
            return ReleaseHttpResponse(200, {}, payload[:-1] + bytes([payload[-1] ^ 1]))
        return fetch(url, maximum_bytes, accept)

    with PackageMirror(changed) as mirror:
        index = httpx.get(_local_url(mirror) + "dependency/", timeout=5)
        link = re.search(r'href="([^"]+)"', index.text)
        assert link is not None
        response = httpx.get(_file_url(mirror, link.group(1)), timeout=5)
        assert response.status_code == 502
        assert mirror.errors


def test_mirror_rejects_direct_url_before_pip_receives_wheel() -> None:
    fetch, _, _ = _fetcher(_wheel(direct_url=True))
    with PackageMirror(fetch) as mirror:
        index = httpx.get(_local_url(mirror) + "dependency/", timeout=5)
        link = re.search(r'href="([^"]+)"', index.text)
        assert link is not None
        file_url = _file_url(mirror, link.group(1))
        response = httpx.get(file_url, timeout=5)
        assert response.status_code == 502
        assert mirror.errors


def test_official_fetch_refuses_other_origins_before_dialing() -> None:
    with pytest.raises(PackageMirrorError, match="allowlisted"):
        _official_fetch("https://unreviewed.example/simple/dep/", 1024, "application/json")


def test_official_fetch_rejects_private_dns_before_dialing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "benchmarks.product_reproduction.release._resolve_public_addresses",
        lambda host, port: ("127.0.0.1",),
    )
    monkeypatch.setattr(
        "benchmarks.product_reproduction.package_mirror._pinned_https_get",
        lambda *args, **kwargs: pytest.fail("private address was dialed"),
    )

    with pytest.raises(ReleaseResolutionError, match="non-public"):
        _official_fetch("https://pypi.org/simple/dependency/", 1024, "application/json")


def test_official_fetch_retries_temporary_origin_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def temporary(target: object, maximum_bytes: int, *, accept: str) -> ReleaseHttpResponse:
        calls.append(1)
        return ReleaseHttpResponse(503 if len(calls) == 1 else 200, {}, b"ready")

    monkeypatch.setattr(
        "benchmarks.product_reproduction.package_mirror._pinned_https_get", temporary
    )
    response = _official_fetch("https://pypi.org/simple/dependency/", 1024, "application/json")
    assert response.status_code == 200
    assert len(calls) == 2


def test_direct_url_wheel_metadata_is_rejected() -> None:
    with pytest.raises(PackageMirrorError, match="direct-URL"):
        validate_wheel_metadata(_wheel(direct_url=True))
