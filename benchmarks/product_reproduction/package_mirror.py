from __future__ import annotations

import hashlib
import html
import io
import json
import re
import secrets
import threading
import zipfile
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urlsplit

from .release import (
    ReleaseHttpResponse,
    ReleaseResolutionError,
    _pin_release_url,
    _pinned_https_get,
    _retry_after_seconds,
)
from .retry import (
    AcquisitionRetryExhausted,
    RetryableAcquisitionError,
    RetryPolicy,
    run_acquisition_with_retry,
)

PACKAGE_NAME = re.compile(r"^[a-z0-9]+(?:[-_.][a-z0-9]+)*$")
WHEEL_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*\.whl$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
MAX_SIMPLE_BYTES = 32 * 1024 * 1024
MAX_WHEEL_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
Fetch = Callable[[str, int, str], ReleaseHttpResponse]


class PackageMirrorError(ValueError):
    """An official package index response cannot be safely mirrored."""


def _official_fetch(url: str, maximum_bytes: int, accept: str) -> ReleaseHttpResponse:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise PackageMirrorError("official package URL is not a canonical HTTPS URL")
    if parsed.netloc == "pypi.org":
        if not re.fullmatch(r"/simple/[a-z0-9_.-]+/", parsed.path):
            raise PackageMirrorError("official package index path is invalid")
    elif parsed.netloc == "files.pythonhosted.org":
        if not parsed.path.startswith("/packages/") or not parsed.path.endswith(".whl"):
            raise PackageMirrorError("official package file path is invalid")
    else:
        raise PackageMirrorError("official package URL left the allowlisted origins")
    def attempt() -> ReleaseHttpResponse:
        response = _pinned_https_get(_pin_release_url(url), maximum_bytes, accept=accept)
        if response.status_code == 429 or 500 <= response.status_code < 600:
            retry_after = _retry_after_seconds(
                response.headers.get("Retry-After") or response.headers.get("retry-after")
            )
            raise RetryableAcquisitionError(
                "official package origin is temporarily unavailable",
                category="package-origin-http",
                retry_after_seconds=min(retry_after, 30.0) if retry_after is not None else None,
            )
        return response

    response, _ = run_acquisition_with_retry(
        attempt,
        policy=RetryPolicy(max_attempts=4, max_elapsed_seconds=120.0),
        operation_class="package-origin-acquisition",
    )
    return response


def validate_wheel_metadata(payload: bytes) -> None:
    """Reject wheels that could make pip fetch direct dependency URLs."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = [
                info for info in archive.infolist()
                if info.filename.endswith(".dist-info/METADATA")
            ]
            if len(entries) != 1 or entries[0].file_size > MAX_METADATA_BYTES:
                raise PackageMirrorError("wheel has missing or oversized distribution metadata")
            with archive.open(entries[0]) as handle:
                metadata = handle.read(MAX_METADATA_BYTES + 1)
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise PackageMirrorError("wheel metadata could not be inspected") from exc
    if len(metadata) > MAX_METADATA_BYTES:
        raise PackageMirrorError("wheel metadata exceeds size limit")
    message = BytesParser().parsebytes(metadata, headersonly=True)
    if any("@" in requirement for requirement in message.get_all("Requires-Dist", [])):
        raise PackageMirrorError("wheel declares a direct-URL dependency")


class PackageMirror:
    """Expose a run-local pip index whose upstream requests are independently IP-pinned."""

    def __init__(self, fetch: Fetch = _official_fetch) -> None:
        self._fetch = fetch
        self._token = secrets.token_urlsafe(24)
        self._links: dict[tuple[str, str], tuple[str, int]] = {}
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.errors: list[str] = []

    @property
    def index_url(self) -> str:
        if self._server is None:
            raise PackageMirrorError("package mirror is not running")
        return f"http://host.docker.internal:{self._server.server_port}/{self._token}/simple/"

    def __enter__(self) -> PackageMirror:
        mirror = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                mirror._handle(self)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        try:
            parsed = urlsplit(handler.path)
            if parsed.query or parsed.fragment:
                raise PackageMirrorError("package mirror request has unsupported parameters")
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == [self._token, "simple"]:
                payload, content_type = self._index(parts[2]), "text/html; charset=utf-8"
            elif len(parts) == 4 and parts[:2] == [self._token, "files"]:
                payload, content_type = self._wheel(parts[2], parts[3]), "application/octet-stream"
            else:
                handler.send_error(404)
                return
            handler.send_response(200)
            handler.send_header("Content-Type", content_type)
            handler.send_header("Content-Length", str(len(payload)))
            handler.end_headers()
            handler.wfile.write(payload)
        except (
            AcquisitionRetryExhausted, PackageMirrorError, ReleaseResolutionError,
            RetryableAcquisitionError, ValueError,
        ) as exc:
            self.errors.append(str(exc))
            handler.send_error(502, "official package mirror rejected response")

    def _index(self, name: str) -> bytes:
        if len(name) > 128 or not PACKAGE_NAME.fullmatch(name):
            raise PackageMirrorError("package name is invalid")
        normalized = re.sub(r"[-_.]+", "-", name.lower())
        response = self._fetch(
            f"https://pypi.org/simple/{normalized}/",
            MAX_SIMPLE_BYTES,
            "application/vnd.pypi.simple.v1+json",
        )
        if response.status_code != 200 or len(response.payload) > MAX_SIMPLE_BYTES:
            raise PackageMirrorError(
                f"official package index request failed for {normalized}: HTTP {response.status_code}"
            )
        try:
            document = json.loads(response.payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PackageMirrorError("official package index returned invalid JSON") from exc
        files = document.get("files") if isinstance(document, dict) else None
        if not isinstance(files, list) or len(files) > 30_000:
            raise PackageMirrorError("official package index has invalid inventory")
        links: list[str] = []
        for entry in files:
            if not isinstance(entry, dict) or entry.get("yanked"):
                continue
            filename = entry.get("filename")
            hashes = entry.get("hashes")
            digest = hashes.get("sha256") if isinstance(hashes, dict) else None
            url = entry.get("url")
            size = entry.get("size")
            if not isinstance(filename, str) or not WHEEL_NAME.fullmatch(filename):
                continue
            if (
                not isinstance(digest, str) or not SHA256.fullmatch(digest)
                or not isinstance(url, str) or not isinstance(size, int)
                or isinstance(size, bool) or not 0 < size <= MAX_WHEEL_BYTES
            ):
                raise PackageMirrorError("official wheel index lacks immutable identity")
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https" or parsed.netloc != "files.pythonhosted.org"
                or parsed.query or parsed.fragment or not parsed.path.startswith("/packages/")
                or not parsed.path.endswith("/" + filename)
            ):
                raise PackageMirrorError("official wheel index contains an untrusted URL")
            with self._lock:
                existing = self._links.setdefault((digest, filename), (url, size))
            if existing != (url, size):
                raise PackageMirrorError("official wheel index has conflicting identity")
            requires_python = entry.get("requires-python")
            marker = (
                f' data-requires-python="{html.escape(requires_python, quote=True)}"'
                if isinstance(requires_python, str) else ""
            )
            links.append(
                f'<a href="/{self._token}/files/{digest}/{filename}#sha256={digest}"'
                f'{marker}>{html.escape(filename)}</a>'
            )
        return ("<!doctype html><html><body>" + "\n".join(links) + "</body></html>").encode()

    def _wheel(self, digest: str, filename: str) -> bytes:
        if not SHA256.fullmatch(digest) or not WHEEL_NAME.fullmatch(filename):
            raise PackageMirrorError("package mirror wheel identity is invalid")
        with self._lock:
            source = self._links.get((digest, filename))
        if source is None:
            raise PackageMirrorError("package mirror wheel was not listed")
        url, expected_size = source
        response = self._fetch(url, expected_size, "application/octet-stream")
        if response.status_code != 200 or len(response.payload) != expected_size:
            raise PackageMirrorError("official wheel download changed size or status")
        if hashlib.sha256(response.payload).hexdigest() != digest:
            raise PackageMirrorError("official wheel download changed digest")
        validate_wheel_metadata(response.payload)
        return response.payload
