"""A callback listener that records which probe URLs a tool actually fetched.

The SSRF check hands the server URLs that all point at this listener's port, each spelled
differently. A request arriving here is proof the tool dialled that spelling. Nothing here
leaves the machine: the listener binds loopback unless told otherwise, and the probe URLs are
the listener's own addresses, never a cloud metadata endpoint or anyone else's host.

The listener answers only for tokens the checker registered with ``expect()``. A path that
names anything else is 404, is never recorded, and never appears in a response header: the
``Location`` of a redirect is built from the registered copy of the token, not from the
request.
"""

from __future__ import annotations

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional

REDIRECT_PREFIX = "/redirect/"
HIT_PREFIX = "/hit/"
REDIRECTED_SUFFIX = "-redirected"


class _Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.expected: Dict[str, str] = {}
        self.hits: Dict[str, List[dict]] = {}

    def expect(self, token: str) -> None:
        with self.lock:
            self.expected[token] = token

    def registered(self, token: str) -> Optional[str]:
        """The checker's own copy of ``token`` if it was registered, else None."""
        with self.lock:
            return self.expected.get(token)

    def record(self, token: str, peer: str) -> None:
        with self.lock:
            self.hits.setdefault(token, []).append({"peer": peer, "at": time.time()})

    def seen(self, token: str) -> bool:
        with self.lock:
            return token in self.hits


class _Handler(BaseHTTPRequestHandler):
    recorder: _Recorder
    redirect_base: str
    server_version = "mcp-ssrf-check"
    sys_version = ""

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - signature fixed by the base class
        return

    def _empty(self, status: int, location: Optional[str] = None) -> None:
        self.send_response(status)
        if location is not None:
            self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _serve(self, with_body: bool) -> None:
        path = self.path.split("?", 1)[0]
        peer = self.client_address[0]
        if path.startswith(REDIRECT_PREFIX):
            token = self.recorder.registered(path[len(REDIRECT_PREFIX):])
            if token is None:
                self._empty(404)
                return
            self.recorder.record(token, peer)
            self._empty(302, f"{self.redirect_base}{HIT_PREFIX}{token}{REDIRECTED_SUFFIX}")
            return
        if path.startswith(HIT_PREFIX):
            token = self.recorder.registered(path[len(HIT_PREFIX):])
            if token is None:
                self._empty(404)
                return
            self.recorder.record(token, peer)
        body = b"mcp-ssrf-check callback\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - name fixed by the base class
        self._serve(True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve(False)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self._serve(True)


class _V6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


class CallbackListener:
    """Listen on ``host`` (IPv4) and, when ``host`` is loopback, on ``::1`` at the same port.

    ``redirect_host`` is the address a redirect points the fetching tool at. It must be an
    address this listener answers on, and it should be one the server's guard refuses when
    given directly; otherwise the redirect probe shows nothing.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0, ipv6: bool = True, redirect_host: Optional[str] = None) -> None:
        self.recorder = _Recorder()
        self._servers: List[ThreadingHTTPServer] = []
        self._threads: List[threading.Thread] = []
        self.host = host
        self.port = port
        self.redirect_host = redirect_host or ("127.0.0.1" if host in ("127.0.0.1", "localhost", "0.0.0.0") else host)
        self.ipv6_bound = False
        self._want_ipv6 = ipv6

    def __enter__(self) -> "CallbackListener":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    def start(self) -> None:
        handler = type("Handler", (_Handler,), {"recorder": self.recorder, "redirect_base": ""})
        v4 = ThreadingHTTPServer((self.host, self.port), handler)
        v4.daemon_threads = True
        self.port = v4.server_address[1]
        redirect_host = f"[{self.redirect_host}]" if ":" in self.redirect_host and not self.redirect_host.startswith("[") else self.redirect_host
        handler.redirect_base = f"http://{redirect_host}:{self.port}"
        self._servers.append(v4)
        if self._want_ipv6 and self.host in ("127.0.0.1", "localhost"):
            try:
                v6 = _V6Server(("::1", self.port), handler)
                v6.daemon_threads = True
                self._servers.append(v6)
                self.ipv6_bound = True
            except OSError:
                self.ipv6_bound = False
        for server in self._servers:
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        for server in self._servers:
            server.shutdown()
            server.server_close()
        self._servers.clear()

    def expect(self, token: str) -> None:
        """Register a token the checker is about to send; only registered tokens are answered."""
        self.recorder.expect(token)
        self.recorder.expect(f"{token}{REDIRECTED_SUFFIX}")

    def seen(self, token: str) -> bool:
        return self.recorder.seen(token)

    def wait_for(self, token: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.recorder.seen(token):
                return True
            time.sleep(0.02)
        return self.recorder.seen(token)

    def peers(self, token: str) -> Optional[List[str]]:
        with self.recorder.lock:
            entries = self.recorder.hits.get(token)
            return sorted({e["peer"] for e in entries}) if entries else None
