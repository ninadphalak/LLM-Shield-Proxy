from __future__ import annotations

import hmac
import socket
import socketserver
import threading
from dataclasses import dataclass
from ipaddress import AddressValueError, IPv4Address

MAX_HEADER_BYTES = 16 * 1024
BUFFER_BYTES = 64 * 1024


@dataclass(frozen=True)
class BridgeSnapshot:
    forwarded_requests: int
    unauthorized_requests: int
    handoff_requests: int


class CaptureBridge:
    """Expose a loopback-only v2 capture to one Docker gateway across case restarts."""

    def __init__(
        self, *, backend_port: int, upstream_key: str,
        bind_host: str = "127.0.0.1", container_host: str = "host.docker.internal",
    ) -> None:
        if not 0 < backend_port < 65536:
            raise ValueError("capture backend port must be 1..65535")
        try:
            address = IPv4Address(bind_host)
        except AddressValueError as exc:
            raise ValueError("capture bind host must be a local IPv4 address") from exc
        if address.is_unspecified or address.is_multicast or not (address.is_private or address.is_loopback):
            raise ValueError("capture bind host must be a specific private or loopback address")
        if container_host not in ("host.docker.internal", bind_host):
            raise ValueError("capture container host must name the Docker host bridge")
        if (
            not upstream_key or len(upstream_key) > 256 or not upstream_key.isascii()
            or any(character in upstream_key for character in "\r\n\x00")
        ):
            raise ValueError("synthetic upstream key is invalid")
        self.backend_port = backend_port
        self.bind_host = bind_host
        self.container_host = container_host
        self._expected_authorization = f"Bearer {upstream_key}".encode("ascii")
        self._server: socketserver.ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._forwarded_requests = 0
        self._unauthorized_requests = 0
        self._handoff_requests = 0

    @property
    def host_port(self) -> int:
        if self._server is None:
            raise RuntimeError("capture bridge is not running")
        return self._server.server_address[1]

    @property
    def local_url(self) -> str:
        return f"http://{self.bind_host}:{self.host_port}"

    @property
    def container_url(self) -> str:
        return f"http://{self.container_host}:{self.host_port}"

    def snapshot(self) -> BridgeSnapshot:
        with self._lock:
            return BridgeSnapshot(
                forwarded_requests=self._forwarded_requests,
                unauthorized_requests=self._unauthorized_requests,
                handoff_requests=self._handoff_requests,
            )

    def __enter__(self) -> CaptureBridge:
        bridge = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                bridge._handle(self.request)

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = False
            daemon_threads = True
            block_on_close = False

        self._server = Server((self.bind_host, 0), Handler)
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

    @staticmethod
    def _respond(client: socket.socket, status: int, reason: str) -> None:
        try:
            client.sendall(
                f"HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
            )
        except OSError:
            pass

    def _authorized(self, header: bytes) -> bool:
        lines = header.split(b"\r\n")
        if not lines or not lines[0].startswith(b"POST /v1/chat/completions "):
            return False
        values = [
            line.split(b":", 1)[1].strip()
            for line in lines[1:]
            if line.lower().startswith(b"authorization:")
        ]
        return len(values) == 1 and hmac.compare_digest(values[0], self._expected_authorization)

    @staticmethod
    def _forward(source: socket.socket, destination: socket.socket) -> None:
        try:
            while True:
                block = source.recv(BUFFER_BYTES)
                if not block:
                    break
                destination.sendall(block)
        except OSError:
            pass
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    def _handle(self, client: socket.socket) -> None:
        client.settimeout(65)
        prefix = bytearray()
        try:
            while b"\r\n\r\n" not in prefix:
                chunk = client.recv(4096)
                if not chunk:
                    return
                prefix.extend(chunk)
                if len(prefix) > MAX_HEADER_BYTES + 4096:
                    self._respond(client, 431, "Request Header Fields Too Large")
                    return
            header_end = prefix.index(b"\r\n\r\n")
            if header_end > MAX_HEADER_BYTES:
                self._respond(client, 431, "Request Header Fields Too Large")
                return
            if not self._authorized(bytes(prefix[:header_end])):
                with self._lock:
                    self._unauthorized_requests += 1
                self._respond(client, 403, "Forbidden")
                return
            try:
                backend = socket.create_connection(("127.0.0.1", self.backend_port), timeout=5)
            except OSError:
                with self._lock:
                    self._handoff_requests += 1
                self._respond(client, 503, "Capture Handoff")
                return
            with backend:
                backend.settimeout(65)
                backend.sendall(prefix)
                with self._lock:
                    self._forwarded_requests += 1
                upload = threading.Thread(target=self._forward, args=(client, backend), daemon=True)
                upload.start()
                self._forward(backend, client)
                upload.join(timeout=5)
        except OSError:
            return
