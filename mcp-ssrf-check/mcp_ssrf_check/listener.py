"""A callback listener that records which probe URLs a tool actually fetched.

The SSRF check hands the server URLs that all point at this listener's port on loopback,
each spelled differently. A request arriving here is proof the tool dialled that spelling.
Nothing here leaves the machine: the listener binds loopback unless told otherwise, and the
probe URLs are the listener's own addresses, never a cloud metadata endpoint or anyone
else's host.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional

REDIRECT_PREFIX = "/redirect/"
HIT_PREFIX = "/hit/"

# Tokens are minted by the checker: a hex nonce, a spelling label, an optional suffix. Anything
# else in a path is not ours, is never recorded, and never reaches a response header.
_TOKEN = re.compile(r"^[A-Za-z0-9-]{1,80}$")


class _Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.hits: Dict[str, List[dict]] = {}

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

    def _serve(self, with_body: bool) -> None:
        path = self.path.split("?", 1)[0]
        peer = self.client_address[0]
        if path.startswith(REDIRECT_PREFIX):
            token = path[len(REDIRECT_PREFIX):]
            if not _TOKEN.match(token):
                self._not_found()
                return
            self.recorder.record(token, peer)
            self.send_response(302)
            self.send_header("Location", f"{self.redirect_base}{HIT_PREFIX}{token}-redirected")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path.startswith(HIT_PREFIX):
            token = path[len(HIT_PREFIX):]
            if not _TOKEN.match(token):
                self._not_found()
                return
            self.recorder.record(token, peer)
        body = b"mcp-ssrf-check callback\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def _not_found(self) -> None:
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

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
    """Listen on IPv4 loopback (or ``host``) and, when possible, IPv6 loopback on the same port."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, ipv6: bool = True) -> None:
        self.recorder = _Recorder()
        self._servers: List[ThreadingHTTPServer] = []
        self._threads: List[threading.Thread] = []
        self.host = host
        self.port = port
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
        handler.redirect_base = f"http://127.0.0.1:{self.port}"
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
