"""The smallest Streamable HTTP client that can speak both MCP lifecycles.

Two lifecycles exist in deployed servers:

* Stateful, protocol versions 2025-03-26 through 2025-11-25: an ``initialize`` handshake,
  an optional ``Mcp-Session-Id`` header minted by the server, HTTP DELETE to end a session,
  HTTP 404 for a terminated session.
* Stateless, protocol version 2026-07-28 and the current draft: no handshake, every request
  carries ``_meta`` with the protocol version and client info, and the ``MCP-Protocol-Version``
  and ``Mcp-Method`` headers are required.

The checker needs only enough of either to send one request as a legitimate client, and then
to send the same request with one header changed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional

import httpx

from . import __version__

STATELESS_VERSION = "2026-07-28"
STATEFUL_VERSION = "2025-11-25"
# Protocol versions are dates. The negotiated value is sent back in a header and written into
# the report and the job summary, so anything else the server returns is ignored, not echoed.
PROTOCOL_VERSION_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
CLIENT_INFO = {"name": "mcp-ssrf-check", "version": __version__}

NAMED_METHODS = {"tools/call": "name", "resources/read": "uri", "prompts/get": "name"}


class TargetUnreachable(Exception):
    """The server did not answer a legitimate request in either lifecycle."""


@dataclass
class HttpResult:
    status: int
    headers: Dict[str, str]
    message: Optional[Dict[str, Any]]
    body_kind: str
    content_type: str = ""

    @property
    def has_result(self) -> bool:
        return isinstance(self.message, dict) and "result" in self.message

    @property
    def jsonrpc_error(self) -> Optional[Dict[str, Any]]:
        if isinstance(self.message, dict) and isinstance(self.message.get("error"), dict):
            return self.message["error"]
        return None

    def summary(self) -> Dict[str, Any]:
        """Status, body kind and message shape only. No response text is ever copied here:
        the report is meant to be uploaded as a CI artifact, and a rejection page from the
        server under test may carry a stack trace or an internal path."""
        out: Dict[str, Any] = {"status": self.status, "body": self.body_kind}
        if self.content_type:
            out["content_type"] = self.content_type
        if self.has_result:
            out["jsonrpc"] = "result"
        elif self.jsonrpc_error is not None:
            out["jsonrpc"] = "error"
            out["error_code"] = self.jsonrpc_error.get("code")
        return out


@dataclass
class Lifecycle:
    version: str
    stateless: bool
    session_id: Optional[str] = None

    @property
    def name(self) -> str:
        return "stateless" if self.stateless else "stateful"


def _meta(version: str) -> Dict[str, Any]:
    return {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": CLIENT_INFO,
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def opening_request(version: str, stateless: bool) -> Dict[str, Any]:
    """The first request a client of this lifecycle sends, which any server must accept."""
    if stateless:
        return {"method": "server/discover", "params": {}}
    return {
        "method": "initialize",
        "params": {"protocolVersion": version, "capabilities": {}, "clientInfo": CLIENT_INFO},
    }


def build_headers(
    method: str,
    params: Dict[str, Any],
    version: str,
    *,
    session_id: Optional[str] = None,
    extra: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": version,
        "mcp-method": method,
    }
    name_key = NAMED_METHODS.get(method)
    if name_key and isinstance(params.get(name_key), str) and params[name_key].isascii():
        headers["mcp-name"] = params[name_key]
    if session_id:
        headers["mcp-session-id"] = session_id
    if extra:
        headers.update({k.lower(): v for k, v in extra.items()})
    return headers


def _sse_messages(lines: Iterator[str]) -> Iterator[Any]:
    data: list = []
    for line in lines:
        line = line.rstrip("\r\n")
        if line == "":
            if data:
                try:
                    yield json.loads("\n".join(data))
                except ValueError:
                    pass
                data = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if field == "data":
            data.append(value[1:] if value.startswith(" ") else value)
    if data:
        try:
            yield json.loads("\n".join(data))
        except ValueError:
            pass


def post(
    client: httpx.Client,
    url: str,
    method: str,
    params: Dict[str, Any],
    lifecycle: Lifecycle,
    *,
    req_id: Optional[int] = 1,
    extra_headers: Optional[Dict[str, str]] = None,
    session_id: Optional[str] = None,
    use_session: bool = True,
) -> HttpResult:
    """POST one JSON-RPC message and return the response message that carries ``req_id``.

    ``req_id=None`` sends a notification. ``session_id`` overrides the lifecycle's own
    (a fabricated or terminated one, for the session checks); ``use_session=False`` sends none.
    """
    params = dict(params)
    if lifecycle.stateless:
        params["_meta"] = _meta(lifecycle.version)
    body: Dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
    if req_id is not None:
        body["id"] = req_id
    sid = session_id if session_id is not None else (lifecycle.session_id if use_session else None)
    headers = build_headers(method, params, lifecycle.version, session_id=sid, extra=extra_headers)

    with client.stream("POST", url, content=json.dumps(body).encode("utf-8"), headers=headers) as response:
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        resp_headers = {k.lower(): v for k, v in response.headers.items()}
        if content_type == "text/event-stream":
            message = None
            for candidate in _sse_messages(response.iter_lines()):
                if isinstance(candidate, dict) and candidate.get("id") == req_id and ("result" in candidate or "error" in candidate):
                    message = candidate
                    break
            return HttpResult(response.status_code, resp_headers, message, "sse", content_type)

        raw = response.read()
        if not raw:
            return HttpResult(response.status_code, resp_headers, None, "empty", content_type)
        try:
            parsed = json.loads(raw)
        except ValueError:
            return HttpResult(response.status_code, resp_headers, None, "other", content_type)
        message = parsed if isinstance(parsed, dict) else None
        return HttpResult(response.status_code, resp_headers, message, "json", content_type)


def delete_session(client: httpx.Client, url: str, lifecycle: Lifecycle, session_id: str) -> int:
    headers = {"mcp-protocol-version": lifecycle.version, "mcp-session-id": session_id}
    return client.delete(url, headers=headers).status_code


def open_lifecycle(client: httpx.Client, url: str, preferred: Optional[str] = None) -> Lifecycle:
    """Find a lifecycle the server accepts, as a legitimate client would.

    Modern first, then the stateful handshake, as the transport specification recommends.
    ``preferred`` pins one lifecycle and skips the other.
    """
    attempts = []
    if preferred in (None, "stateless"):
        attempts.append(Lifecycle(STATELESS_VERSION, True))
    if preferred in (None, "stateful"):
        attempts.append(Lifecycle(STATEFUL_VERSION, False))

    failures = []
    for candidate in attempts:
        opener = opening_request(candidate.version, candidate.stateless)
        try:
            result = post(client, url, opener["method"], opener["params"], candidate, use_session=False)
        except httpx.HTTPError as exc:
            raise TargetUnreachable(f"{type(exc).__name__} while contacting {url}") from exc
        if 200 <= result.status < 300 and result.has_result:
            if not candidate.stateless:
                negotiated = result.message.get("result", {}).get("protocolVersion") if result.message else None
                if isinstance(negotiated, str) and PROTOCOL_VERSION_PATTERN.fullmatch(negotiated):
                    candidate.version = negotiated
                candidate.session_id = result.headers.get("mcp-session-id")
                post(client, url, "notifications/initialized", {}, candidate, req_id=None)
            return candidate
        failures.append(f"{candidate.name} ({candidate.version}): HTTP {result.status}, {result.body_kind}")
    raise TargetUnreachable("; ".join(failures))
