from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from benchmarks.product_reproduction import release as release_module
from benchmarks.product_reproduction.catalog import _make_catalog
from benchmarks.product_reproduction.release import (
    ReleaseResolutionError,
    fetch_release_json,
    resolve_release,
    select_run,
)


class _Response:
    def __init__(self, payload: bytes, *, url: str, content_length: str | None = None) -> None:
        self._payload = payload
        self._url = url
        self.headers = {} if content_length is None else {"Content-Length": content_length}

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, maximum: int) -> bytes:
        return self._payload[:maximum]


def _pypi_document(*, version: str = "1.6.7", digest: str = "a" * 64) -> dict[str, Any]:
    filename = f"llm_shield_proxy-{version}-py3-none-any.whl"
    return {
        "info": {"name": "llm-shield-proxy", "version": version},
        "urls": [
            {
                "filename": filename,
                "packagetype": "bdist_wheel",
                "url": f"https://files.pythonhosted.org/packages/aa/{filename}",
                "digests": {"sha256": digest},
                "size": 209_709,
                "yanked": False,
            },
            {
                "filename": f"llm_shield_proxy-{version}.tar.gz",
                "packagetype": "sdist",
                "url": f"https://files.pythonhosted.org/packages/bb/llm_shield_proxy-{version}.tar.gz",
                "digests": {"sha256": "b" * 64},
                "size": 371_282,
                "yanked": False,
            },
        ],
    }


@pytest.fixture
def reviewed_catalog(valid_catalog: dict[str, Any]):
    document = copy.deepcopy(valid_catalog)
    source = document["release_sources"][0]
    source.update(
        source_type="pypi-json",
        api_base_url="https://pypi.org",
        package="llm-shield-proxy",
        version_pattern=r"^\d+\.\d+\.\d+$",
        assets={
            "name_pattern": r"^llm_shield_proxy-\d+\.\d+\.\d+-py3-none-any\.whl$",
            "media_types": ["application/zip"],
        },
    )
    source.pop("repository", None)
    target = document["targets"][0]
    target["accepted_baselines"][0].update(
        id="llm-shield-proxy-1.6.6-response-on",
        released_version="1.6.6",
        artifact_reference="llm_shield_proxy-1.6.6-py3-none-any.whl",
        artifact_identity="sha256:" + "c" * 64,
    )
    return _make_catalog(document)


def test_latest_release_always_selects_measure(reviewed_catalog) -> None:
    selection = select_run(
        reviewed_catalog,
        target_id="test-gateway-default",
        release_selector="latest-release",
        requested_mode="auto",
    )

    assert selection.mode == "measure"
    assert selection.baseline is None


def test_accepted_baseline_auto_selects_reproduce(reviewed_catalog) -> None:
    selection = select_run(
        reviewed_catalog,
        target_id="test-gateway-default",
        release_selector="llm-shield-proxy-1.6.6-response-on",
        requested_mode="auto",
    )

    assert selection.mode == "reproduce"
    assert selection.version == "1.6.6"
    assert selection.expected_reference == "llm_shield_proxy-1.6.6-py3-none-any.whl"
    assert selection.expected_identity == "sha256:" + "c" * 64


@pytest.mark.parametrize(
    ("selector", "mode"),
    [("latest-release", "reproduce"), ("llm-shield-proxy-1.6.6-response-on", "measure")],
)
def test_invalid_selector_mode_combinations_fail_closed(reviewed_catalog, selector: str, mode: str) -> None:
    with pytest.raises(ReleaseResolutionError, match="invalid mode"):
        select_run(
            reviewed_catalog,
            target_id="test-gateway-default",
            release_selector=selector,
            requested_mode=mode,
        )


def test_resolves_latest_stable_wheel_from_allowlisted_pypi_source(reviewed_catalog) -> None:
    source = reviewed_catalog.release_sources[0]
    requested: list[tuple[str, int]] = []

    def fetch(url: str, maximum_bytes: int) -> dict[str, Any]:
        requested.append((url, maximum_bytes))
        return _pypi_document()

    resolved = resolve_release(source, version=None, fetch_json=fetch)

    assert requested == [("https://pypi.org/pypi/llm-shield-proxy/json", 1_048_576)]
    assert resolved.version == "1.6.7"
    assert resolved.release_state == "stable"
    assert resolved.artifact_identity == "sha256:" + "a" * 64
    assert resolved.artifact_reference == "llm_shield_proxy-1.6.7-py3-none-any.whl"
    assert resolved.artifact_url.startswith("https://files.pythonhosted.org/")
    assert resolved.release_url == "https://pypi.org/project/llm-shield-proxy/1.6.7/"


def test_resolves_exact_baseline_and_requires_expected_identity(reviewed_catalog) -> None:
    source = reviewed_catalog.release_sources[0]

    resolved = resolve_release(
        source,
        version="1.6.6",
        expected_reference="llm_shield_proxy-1.6.6-py3-none-any.whl",
        expected_identity="sha256:" + "c" * 64,
        fetch_json=lambda _url, _limit: _pypi_document(version="1.6.6", digest="c" * 64),
    )

    assert resolved.version == "1.6.6"
    assert resolved.artifact_identity == "sha256:" + "c" * 64

    with pytest.raises(ReleaseResolutionError, match="identity"):
        resolve_release(
            source,
            version="1.6.6",
            expected_reference="llm_shield_proxy-1.6.6-py3-none-any.whl",
            expected_identity="sha256:" + "d" * 64,
            fetch_json=lambda _url, _limit: _pypi_document(version="1.6.6", digest="c" * 64),
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda d: d["info"].update(version="1.6.7rc1"), "version"),
        (lambda d: d["info"].update(name="different-package"), "package name"),
        (lambda d: d["urls"][0].update(yanked=True), "yanked"),
        (lambda d: d["urls"][0].update(url="https://example.test/file.whl"), "artifact URL"),
        (lambda d: d["urls"][0]["digests"].update(sha256="not-a-digest"), "SHA-256"),
        (lambda d: d["urls"][0].update(filename="wrong.whl"), "wheel"),
    ],
)
def test_rejects_untrusted_or_ineligible_pypi_metadata(reviewed_catalog, mutation, message: str) -> None:
    document = _pypi_document()
    mutation(document)

    with pytest.raises(ReleaseResolutionError, match=message):
        resolve_release(
            reviewed_catalog.release_sources[0],
            version=None,
            fetch_json=lambda _url, _limit: document,
        )


def test_fetch_rejects_redirect_outside_allowlisted_origin(monkeypatch) -> None:
    payload = json.dumps(_pypi_document()).encode()
    monkeypatch.setattr(
        release_module,
        "urlopen",
        lambda *_args, **_kwargs: _Response(payload, url="https://example.test/metadata"),
    )

    with pytest.raises(ReleaseResolutionError, match="allowlisted"):
        fetch_release_json("https://pypi.org/pypi/llm-shield-proxy/json", 1_048_576)


@pytest.mark.parametrize("content_length", ["1001", "not-an-integer", "-1"])
def test_fetch_rejects_invalid_or_oversized_declared_length(monkeypatch, content_length: str) -> None:
    payload = json.dumps(_pypi_document()).encode()
    monkeypatch.setattr(
        release_module,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            payload,
            url="https://pypi.org/pypi/llm-shield-proxy/json",
            content_length=content_length,
        ),
    )

    with pytest.raises(ReleaseResolutionError, match="Content-Length|response size"):
        fetch_release_json("https://pypi.org/pypi/llm-shield-proxy/json", 1000)


def test_fetch_rejects_body_larger_than_cap_and_nonfinite_json(monkeypatch) -> None:
    responses = iter(
        [
            _Response(b"x" * 1001, url="https://pypi.org/pypi/llm-shield-proxy/json"),
            _Response(b'{"value":NaN}', url="https://pypi.org/pypi/llm-shield-proxy/json"),
        ]
    )
    monkeypatch.setattr(release_module, "urlopen", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(ReleaseResolutionError, match="response size"):
        fetch_release_json("https://pypi.org/pypi/llm-shield-proxy/json", 1000)
    with pytest.raises(ReleaseResolutionError, match="valid JSON"):
        fetch_release_json("https://pypi.org/pypi/llm-shield-proxy/json", 1000)
