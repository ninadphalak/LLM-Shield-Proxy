"""HTTP/2 upstream negotiation and multiplexing, measured on the wire.

The rest of the suite only ever observed that a shared ``httpx.AsyncClient``
object was reused. That is not evidence of HTTP/2: ``httpx`` falls back to
HTTP/1.1 silently whenever ALPN does not agree on ``h2``, and
``get_http_client``'s lazy fallback path in ``api/main.py`` used to build its
client with ``http2=False`` outright -- which is exactly the client the rest of
the suite gets, because ``conftest.py`` clears ``app.state.http_client`` before
each test. Both paths now construct through the single
``build_upstream_client()`` factory; ``test_lazy_fallback_client_matches_the_pooled_client``
is what holds that.

So these tests run the real proxy under uvicorn (its lifespan builds the
``http2=True`` pooled client) against a real ALPN-negotiating h2 server, and
assert on what the upstream actually observed:

* ``scope["http_version"] == "2"`` at the upstream, and ``http_version ==
  "HTTP/2"`` on a direct client response;
* concurrent proxied requests arriving over a single TCP connection, which is
  the multiplexing property the pooling claim rests on;
* an ALPN-less HTTP/1.1-only server still being proxied correctly, so the
  h2 client degrades rather than failing.

Requires ``hypercorn`` (uvicorn has no HTTP/2 server support) and ``httpx[http2]``.
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import json
import re
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator, Tuple

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import llm_shield_proxy.api.main as main_module

hypercorn = pytest.importorskip("hypercorn", reason="hypercorn provides the h2 test server")
pytest.importorskip("h2", reason="httpx[http2] must be installed to negotiate HTTP/2")

import uvicorn  # noqa: E402
from hypercorn.asyncio import serve as hypercorn_serve  # noqa: E402
from hypercorn.config import Config as HypercornConfig  # noqa: E402

from llm_shield_proxy.api.main import app  # noqa: E402
from llm_shield_proxy.core.config import settings  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_self_signed_cert(directory) -> Tuple[str, str]:
    """A self-signed CA-capable cert for localhost, usable as its own trust root."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    cert_path = directory / "upstream-cert.pem"
    key_path = directory / "upstream-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)


async def _echo_app(scope, receive, send):
    """Reports the protocol version and peer socket the upstream really saw."""
    while True:
        message = await receive()
        if message["type"] != "http.request" or not message.get("more_body"):
            break

    client = scope.get("client") or ("?", 0)
    body = json.dumps(
        {
            "id": "chatcmpl-h2",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "observed": {
                "http_version": scope.get("http_version"),
                "peer": f"{client[0]}:{client[1]}",
                "path": scope.get("path"),
            },
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class _BackgroundServer:
    """Runs an ASGI server on its own thread and event loop."""

    def __init__(self, runner):
        self._runner = runner
        self._loop = asyncio.new_event_loop()
        self._stop = asyncio.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._stop = asyncio.Event()
        self._loop.run_until_complete(self._runner(self._stop))

    def start(self, port: int, timeout: float = 20.0) -> None:
        self._thread.start()
        _wait_for_port(port, timeout)

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=15)


def _wait_for_port(port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"server on port {port} never accepted a connection")


@pytest.fixture(scope="module")
def h2_upstream(tmp_path_factory) -> Iterator[dict]:
    """A real ALPN-negotiating HTTP/2 server (hypercorn over TLS)."""
    cert_path, key_path = _write_self_signed_cert(tmp_path_factory.mktemp("tls"))
    port = _free_port()

    config = HypercornConfig()
    config.bind = [f"127.0.0.1:{port}"]
    config.certfile = cert_path
    config.keyfile = key_path
    config.alpn_protocols = ["h2", "http/1.1"]
    config.accesslog = None
    config.errorlog = None

    server = _BackgroundServer(
        lambda stop: hypercorn_serve(_echo_app, config, shutdown_trigger=stop.wait)
    )
    server.start(port)
    try:
        yield {"port": port, "cert": cert_path, "base_url": f"https://localhost:{port}"}
    finally:
        server.stop()


@pytest.fixture(scope="module")
def h1_only_upstream(tmp_path_factory) -> Iterator[dict]:
    """The same server with ALPN offering http/1.1 only, for the fallback test."""
    cert_path, key_path = _write_self_signed_cert(tmp_path_factory.mktemp("tls-h1"))
    port = _free_port()

    config = HypercornConfig()
    config.bind = [f"127.0.0.1:{port}"]
    config.certfile = cert_path
    config.keyfile = key_path
    config.alpn_protocols = ["http/1.1"]
    config.accesslog = None
    config.errorlog = None

    server = _BackgroundServer(
        lambda stop: hypercorn_serve(_echo_app, config, shutdown_trigger=stop.wait)
    )
    server.start(port)
    try:
        yield {"port": port, "cert": cert_path, "base_url": f"https://localhost:{port}"}
    finally:
        server.stop()


@pytest.fixture
def proxy_over(request) -> Iterator:
    """Start the real proxy under uvicorn, pointed at a TLS upstream.

    Function-scoped on purpose: the autouse fixture in ``conftest.py`` resets
    ``app.state.http_client`` to ``None`` before every test, so the pooled
    ``http2=True`` client has to be rebuilt by the app's lifespan afterwards.
    """
    started = []

    def _start(upstream: dict) -> str:
        settings.UPSTREAM_BASE_URL = upstream["base_url"]
        settings.CA_BUNDLE_FILE = upstream["cert"]
        settings.ENABLE_CANARY_TRIPWIRE = False

        port = _free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)

        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        _wait_for_port(port, timeout=30.0)
        started.append((server, thread))
        return f"http://127.0.0.1:{port}"

    try:
        yield _start
    finally:
        for server, thread in started:
            server.should_exit = True
            thread.join(timeout=20)
        settings.CA_BUNDLE_FILE = None
        settings.UPSTREAM_BASE_URL = "https://api.openai.com"


def _chat(base_url: str, **kwargs) -> httpx.Response:
    return httpx.post(
        f"{base_url}/v1/chat/completions",
        headers={"Authorization": "Bearer sk-proj-mock-key"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
        timeout=30.0,
        **kwargs,
    )


def test_direct_client_negotiates_http2_with_the_test_server(h2_upstream):
    """Sanity floor: the fixture really speaks HTTP/2, so later failures mean something."""
    with httpx.Client(http2=True, verify=h2_upstream["cert"], timeout=15.0) as client:
        response = client.get(f"{h2_upstream['base_url']}/v1/models")

    assert response.http_version == "HTTP/2"
    assert response.json()["observed"]["http_version"] == "2"


def test_http1_client_stays_on_http11_against_the_same_server(h2_upstream):
    """Proves the assertion above is measuring ALPN, not a constant in the server."""
    with httpx.Client(http2=False, verify=h2_upstream["cert"], timeout=15.0) as client:
        response = client.get(f"{h2_upstream['base_url']}/v1/models")

    assert response.http_version == "HTTP/1.1"
    assert response.json()["observed"]["http_version"] == "1.1"


def test_proxy_reaches_its_upstream_over_http2(h2_upstream, proxy_over):
    """The pooled client the app builds at startup negotiates h2 to the upstream."""
    proxy_url = proxy_over(h2_upstream)

    response = _chat(proxy_url)

    assert response.status_code == 200, response.text
    observed = response.json()["observed"]
    assert observed["http_version"] == "2", (
        "the proxy reached its upstream over HTTP/"
        f"{observed['http_version']}, not HTTP/2"
    )
    assert observed["path"] == "/v1/chat/completions"


def test_proxy_multiplexes_concurrent_requests_over_one_connection(h2_upstream, proxy_over):
    """Multiplexing, measured as distinct upstream source ports.

    HTTP/1.1 keep-alive would need one TCP connection per in-flight request;
    HTTP/2 carries them as streams on a single connection. Counting the peer
    sockets the upstream saw distinguishes the two without trusting httpx.
    """
    proxy_url = proxy_over(h2_upstream)

    # Warm the pool first, so the measurement is about multiplexing rather than
    # about several requests racing to open the first connection.
    warmup = _chat(proxy_url)
    assert warmup.status_code == 200
    warm_peer = warmup.json()["observed"]["peer"]

    responses: list[httpx.Response] = []
    with httpx.Client(timeout=30.0) as client:
        def _fire() -> None:
            responses.append(
                client.post(
                    f"{proxy_url}/v1/chat/completions",
                    headers={"Authorization": "Bearer sk-proj-mock-key"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": "hello"}],
                    },
                )
            )

        threads = [threading.Thread(target=_fire) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

    assert len(responses) == 8
    assert all(r.status_code == 200 for r in responses)

    peers = {r.json()["observed"]["peer"] for r in responses}
    assert peers == {warm_peer}, (
        f"8 concurrent proxied requests used {len(peers)} upstream TCP connections "
        f"({peers}); HTTP/2 multiplexing would have reused the pooled one"
    )


def test_proxy_falls_back_cleanly_to_http11(h1_only_upstream, proxy_over):
    """An upstream that refuses h2 in ALPN is still proxied, over HTTP/1.1."""
    proxy_url = proxy_over(h1_only_upstream)

    response = _chat(proxy_url)

    assert response.status_code == 200, response.text
    assert response.json()["observed"]["http_version"] == "1.1"


# ---------------------------------------------------------------------------
# The lazy fallback must not silently differ from the pooled client
# ---------------------------------------------------------------------------


def _pool_config(client: httpx.AsyncClient) -> dict:
    """The connection-pool settings that decide the wire protocol."""
    pool = client._transport._pool  # noqa: SLF001 - no public accessor exists
    return {
        "http2": pool._http2,
        "http1": pool._http1,
        "max_connections": pool._max_connections,
        "max_keepalive_connections": pool._max_keepalive_connections,
    }


def test_lazy_fallback_client_matches_the_pooled_client():
    """`get_http_client`'s fallback must build the same client as the lifespan.

    It used to pass `http2=False` while the lifespan passed `http2=True`, from
    two copies of the same twenty-line block. Any request served before or
    after the lifespan client existed silently dropped to HTTP/1.1, with no
    error and no log line. Both paths now go through `build_upstream_client()`.
    """
    from llm_shield_proxy.api.main import build_upstream_client, get_http_client

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    fallback = get_http_client(request)
    try:
        assert _pool_config(fallback)["http2"] is True, (
            "the lazy fallback client has HTTP/2 disabled; it would silently "
            "serve every request over HTTP/1.1"
        )

        pooled = build_upstream_client()
        try:
            assert _pool_config(fallback) == _pool_config(pooled)
        finally:
            asyncio.run(pooled.aclose())

        # The fallback must also be cached on app.state, not rebuilt per call.
        assert request.app.state.http_client is fallback
        assert get_http_client(request) is fallback
    finally:
        asyncio.run(fallback.aclose())


def test_only_one_place_in_main_constructs_the_upstream_client():
    """Structural guard against the two call sites diverging again.

    The defect was not the boolean; it was that the configuration existed
    twice. If a second `httpx.AsyncClient(...)` construction appears in
    `api/main.py`, the parity test above can start passing while a third path
    quietly disagrees.
    """
    source = Path(main_module.__file__).read_text(encoding="utf-8")
    constructions = re.findall(r"httpx\.AsyncClient\(", source)
    assert len(constructions) == 1, (
        f"expected exactly one httpx.AsyncClient(...) construction in "
        f"api/main.py, found {len(constructions)}"
    )
