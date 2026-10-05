"""A configurable MCP server for the checker's tests: hardened, weak, stateful, stateless.

Only what the checker exercises is implemented: the two opening requests, ``tools/list``,
``tools/call`` for a single ``fetch`` tool, session issue and DELETE, and Host and Origin
validation that can each be switched off. The fetch tool really fetches, using urllib, so
the checker's loopback listener sees a real request when a guard lets one through.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

STATEFUL = "2025-11-25"
STATELESS = "2026-07-28"


def _blocked_by_resolution(url: str) -> bool:
    """A resolve-then-check guard: every address must be public, or the fetch is refused."""
    host = urlsplit(url).hostname
    if not host:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified or ip.is_reserved or ip.is_multicast:
            return True
    return False


def _blocked_by_string(url: str) -> bool:
    """The guard people write first: compare the host text against a short list."""
    return urlsplit(url).hostname in ("127.0.0.1",)


class FakeMcpServer:
    def __init__(
        self,
        *,
        stateless: bool = False,
        sessions: bool = True,
        check_session: bool = True,
        validate_host: bool = True,
        validate_origin: bool = True,
        reject_null_origin: bool = True,
        html_errors: bool = False,
        delete_supported: bool = True,
        fetch_guard: str = "resolve",
        sse: bool = False,
        tool_name: str = "fetch",
        url_argument: str = "url",
        port: int = 0,
        protocol_version: str = STATEFUL,
        unknown_tool_reply: str = "error",
    ) -> None:
        # How `tools/call` for a tool this server does not have, or with the wrong argument,
        # is answered. "error" is a JSON-RPC -32602, which is what the first fake did and
        # what the checker always recognised. "is_error" is what the official Python SDK
        # (1.x and 2.x) actually sends: an ordinary result with `isError: true` and the text
        # "Unknown tool: <name>", indistinguishable on the wire from a tool that refused.
        if unknown_tool_reply not in ("error", "is_error"):
            raise ValueError("unknown_tool_reply must be 'error' or 'is_error'")
        self.unknown_tool_reply = unknown_tool_reply
        self.stateless = stateless
        self.sessions = sessions and not stateless
        self.check_session = check_session
        self.validate_host = validate_host
        self.validate_origin = validate_origin
        self.reject_null_origin = reject_null_origin
        self.html_errors = html_errors
        self.delete_supported = delete_supported
        self.fetch_guard = fetch_guard
        self.sse = sse
        self.tool_name = tool_name
        self.url_argument = url_argument
        self.live_sessions: set = set()
        self.terminated_sessions: set = set()
        self.fetched_urls: list = []
        self.authorization_headers: list = []
        self._lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port = port
        self.protocol_version = protocol_version

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def __enter__(self) -> "FakeMcpServer":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    def start(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

            def _reply(self, status: int, payload: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, as_sse: bool = False) -> None:
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if payload is None:
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if as_sse:
                    body = f"event: message\ndata: {json.dumps(payload)}\n\n".encode("utf-8")
                    self.send_header("Content-Type", "text/event-stream")
                else:
                    body = json.dumps(payload).encode("utf-8")
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _error(self, status: int, req_id: Any, code: int, message: str) -> None:
                self._reply(status, {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})

            def _reject(self, status: int) -> None:
                if not owner.html_errors:
                    self._reply(status, None)
                    return
                body = b"<html><body><pre>DEBUG-PAGE-MARKER Traceback: /srv/app/gate.py line 12</pre></body></html>"
                self.send_response(status)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _gate(self) -> bool:
                # Read the body before any early rejection. Closing a socket with request
                # bytes still unread makes the OS send a reset instead of a clean close, and
                # if the reset beats the client to the response, the client sees a read error
                # instead of the 403/421. That race made the header checks INCONCLUSIVE now
                # and then in full-suite runs, failing tests that expect a clean exit.
                length = int(self.headers.get("Content-Length") or 0)
                self._body = self.rfile.read(length) if length else b""
                with owner._lock:
                    owner.authorization_headers.append(self.headers.get("Authorization"))
                allowed_hosts = {f"127.0.0.1:{owner.port}", f"localhost:{owner.port}", f"[::1]:{owner.port}"}
                if owner.validate_host and self.headers.get("Host") not in allowed_hosts:
                    self._reject(421)
                    return False
                origin = self.headers.get("Origin")
                if owner.validate_origin and origin is not None:
                    allowed = {f"http://{h}" for h in allowed_hosts}
                    if origin == "null":
                        if owner.reject_null_origin:
                            self._reject(403)
                            return False
                    elif origin not in allowed:
                        self._reject(403)
                        return False
                return True

            def do_DELETE(self) -> None:  # noqa: N802
                if not self._gate():
                    return
                if not owner.sessions or not owner.delete_supported:
                    self._reply(405, None)
                    return
                sid = self.headers.get("Mcp-Session-Id")
                with owner._lock:
                    if sid in owner.live_sessions:
                        owner.live_sessions.discard(sid)
                        owner.terminated_sessions.add(sid)
                        self._reply(200, None)
                        return
                self._reply(404, None)

            def do_GET(self) -> None:  # noqa: N802
                self._reply(405, None)

            def do_POST(self) -> None:  # noqa: N802
                if not self._gate():
                    return
                try:
                    body = json.loads(self._body or b"null")
                except ValueError:
                    self._error(400, None, -32700, "parse error")
                    return
                if not isinstance(body, dict):
                    self._error(400, None, -32600, "invalid request")
                    return
                req_id = body.get("id")
                method = body.get("method")
                params = body.get("params") or {}
                if owner.stateless:
                    self._stateless(req_id, method, params)
                else:
                    self._stateful(req_id, method, params)

            def _stateless(self, req_id, method, params) -> None:
                meta = params.get("_meta") or {}
                if meta.get("io.modelcontextprotocol/protocolVersion") != STATELESS:
                    self._error(400, req_id, -32600, "unsupported protocol version")
                    return
                if method == "server/discover":
                    self._reply(200, {"jsonrpc": "2.0", "id": req_id, "result": {"serverInfo": {"name": "fake", "version": "0"}}}, as_sse=owner.sse)
                    return
                self._dispatch(req_id, method, params)

            def _stateful(self, req_id, method, params) -> None:
                if method == "initialize":
                    headers = {}
                    if owner.sessions:
                        sid = f"sess-{len(owner.live_sessions) + len(owner.terminated_sessions) + 1}-{id(self) % 9973}"
                        with owner._lock:
                            owner.live_sessions.add(sid)
                        headers["Mcp-Session-Id"] = sid
                    result = {"protocolVersion": owner.protocol_version, "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "0"}}
                    self._reply(200, {"jsonrpc": "2.0", "id": req_id, "result": result}, headers, as_sse=owner.sse)
                    return
                if owner.sessions and owner.check_session:
                    sid = self.headers.get("Mcp-Session-Id")
                    if not sid:
                        self._error(400, req_id, -32600, "missing session id")
                        return
                    with owner._lock:
                        live = sid in owner.live_sessions
                    if not live:
                        self._reply(404, None)
                        return
                if method == "notifications/initialized":
                    self._reply(202, None)
                    return
                self._dispatch(req_id, method, params)

            def _dispatch(self, req_id, method, params) -> None:
                if method == "tools/list":
                    tools = [{"name": owner.tool_name, "inputSchema": {"type": "object", "properties": {owner.url_argument: {"type": "string"}}}}]
                    self._reply(200, {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}, as_sse=owner.sse)
                    return
                if method == "tools/call":
                    if params.get("name") != owner.tool_name:
                        if owner.unknown_tool_reply == "is_error":
                            text = f"Unknown tool: {params.get('name')}"
                            self._reply(200, {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}], "isError": True}}, as_sse=owner.sse)
                        else:
                            self._error(200, req_id, -32602, "unknown tool")
                        return
                    target = (params.get("arguments") or {}).get(owner.url_argument)
                    if not isinstance(target, str):
                        if owner.unknown_tool_reply == "is_error":
                            text = f"Input validation error: '{owner.url_argument}' is a required property"
                            self._reply(200, {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}], "isError": True}}, as_sse=owner.sse)
                        else:
                            self._error(200, req_id, -32602, f"missing argument {owner.url_argument}")
                        return
                    self._reply(200, {"jsonrpc": "2.0", "id": req_id, "result": owner._fetch(target)}, as_sse=owner.sse)
                    return
                self._error(404, req_id, -32601, "method not found")

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def _fetch(self, target: str) -> Dict[str, Any]:
        blocked = False
        if self.fetch_guard == "resolve":
            blocked = _blocked_by_resolution(target)
        elif self.fetch_guard == "string":
            blocked = _blocked_by_string(target)
        if blocked:
            return {"content": [{"type": "text", "text": "refused: destination not permitted"}], "isError": True}
        with self._lock:
            self.fetched_urls.append(target)
        try:
            # This is the vulnerable fetch tool the checker exists to catch; it runs only in tests.
            with urllib.request.urlopen(target, timeout=2) as response:  # noqa: S310  # codeql[py/full-ssrf]
                size = len(response.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"content": [{"type": "text", "text": f"fetch failed: {type(exc).__name__}"}], "isError": True}
        # The marker lets a test prove that tool output never reaches the checker's report.
        return {"content": [{"type": "text", "text": f"fetched {size} bytes TOOL-OUTPUT-MARKER"}]}


MODES = {
    "hardened": {},
    "weak": {"validate_host": False, "validate_origin": False, "check_session": False, "fetch_guard": "none"},
}


def main() -> None:
    """Serve one mode in the foreground, for the CI job that runs the GitHub Action against it."""
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Serve the checker's test double on loopback.")
    parser.add_argument("--mode", choices=sorted(MODES), required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    with FakeMcpServer(port=args.port, **MODES[args.mode]) as server:
        print(f"{args.mode} test double at {server.url}", flush=True)
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
