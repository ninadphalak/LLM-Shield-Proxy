"""The checks. Each one sends what a legitimate client sends, with one thing changed.

Every check reports what the server did, in terms of HTTP status and JSON-RPC shape. None
of them reads or stores tool output, and none of them sends a request anywhere other than
the server under test and the checker's own loopback listener.
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional

import httpx

from .listener import CallbackListener
from .report import FAIL, INCONCLUSIVE, INFO, PASS, SKIP, CheckResult
from .transport import Lifecycle, delete_session, open_lifecycle, opening_request, post

SPEC_TRANSPORT_SECURITY = (
    "https://modelcontextprotocol.io/specification/draft/basic/transports/streamable-http#security--endpoint"
)
SPEC_SESSIONS = "https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#session-management"
SBP_SSRF = (
    "https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices#server-side-request-forgery-ssrf"
)

# Every spelling below names the listener's own loopback port. They differ only in how the
# host is written, which is exactly what a string-matching guard gets wrong.
LOOPBACK_SPELLINGS = (
    ("direct", "127.0.0.1"),
    ("localhost", "localhost"),
    ("ipv6-loopback", "[::1]"),
    ("ipv4-mapped-ipv6", "[::ffff:127.0.0.1]"),
    ("decimal", "2130706433"),
    ("hex", "0x7f000001"),
    ("octal", "0177.0.0.1"),
    ("short", "127.1"),
    ("unspecified", "0.0.0.0"),
)

JSONRPC_INVALID_PARAMS = -32602
JSONRPC_METHOD_NOT_FOUND = -32601


def _nonce() -> str:
    return secrets.token_hex(6)


def _accepted(result) -> bool:
    return 200 <= result.status < 300 and result.has_result


def _cleanup_session(client: httpx.Client, url: str, lifecycle: Lifecycle, result) -> None:
    """A forged-header request that was accepted may have minted a session; end it."""
    sid = result.headers.get("mcp-session-id")
    if sid and not lifecycle.stateless:
        try:
            delete_session(client, url, lifecycle, sid)
        except httpx.HTTPError:
            pass


def _send_opener(client: httpx.Client, url: str, lifecycle: Lifecycle, extra: Dict[str, str]):
    opener = opening_request(lifecycle.version, lifecycle.stateless)
    return post(client, url, opener["method"], opener["params"], lifecycle, extra_headers=extra, use_session=False)


def check_baseline(client: httpx.Client, url: str, lifecycle: Lifecycle) -> CheckResult:
    """The unmodified opening request. Everything else is read relative to this."""
    try:
        result = _send_opener(client, url, lifecycle, {})
    except httpx.HTTPError as exc:
        return CheckResult("baseline", "Legitimate request accepted", INCONCLUSIVE, f"{type(exc).__name__}", {})
    if _accepted(result):
        _cleanup_session(client, url, lifecycle, result)
        return CheckResult(
            "baseline",
            "Legitimate request accepted",
            PASS,
            f"{lifecycle.name} lifecycle, protocol {lifecycle.version}, HTTP {result.status}",
            {"response": result.summary()},
        )
    return CheckResult(
        "baseline",
        "Legitimate request accepted",
        INCONCLUSIVE,
        f"the unmodified request was refused with HTTP {result.status}; header checks cannot be read",
        {"response": result.summary()},
    )


def check_host_header(client: httpx.Client, url: str, lifecycle: Lifecycle) -> CheckResult:
    forged = f"rebound-{_nonce()}.invalid"
    try:
        result = _send_opener(client, url, lifecycle, {"Host": forged})
    except httpx.HTTPError as exc:
        return CheckResult("host-header", "Host header validated", INCONCLUSIVE, type(exc).__name__, {"host": forged})
    evidence = {"host_sent": forged, "response": result.summary()}
    if _accepted(result):
        _cleanup_session(client, url, lifecycle, result)
        return CheckResult(
            "host-header",
            "Host header validated",
            FAIL,
            f"a request for Host {forged} was served (HTTP {result.status}); a DNS-rebound page reaches this server",
            evidence,
            SPEC_TRANSPORT_SECURITY,
        )
    if 400 <= result.status < 500:
        return CheckResult(
            "host-header", "Host header validated", PASS, f"unknown Host rejected with HTTP {result.status}", evidence, SPEC_TRANSPORT_SECURITY
        )
    return CheckResult(
        "host-header", "Host header validated", INCONCLUSIVE, f"HTTP {result.status} is neither acceptance nor a client error", evidence
    )


def check_origin_header(client: httpx.Client, url: str, lifecycle: Lifecycle) -> CheckResult:
    forged = f"https://evil-{_nonce()}.invalid"
    try:
        result = _send_opener(client, url, lifecycle, {"Origin": forged})
    except httpx.HTTPError as exc:
        return CheckResult("origin-header", "Origin header validated", INCONCLUSIVE, type(exc).__name__, {"origin": forged})
    evidence = {"origin_sent": forged, "response": result.summary()}
    if _accepted(result):
        _cleanup_session(client, url, lifecycle, result)
        return CheckResult(
            "origin-header",
            "Origin header validated",
            FAIL,
            f"a request with Origin {forged} was served (HTTP {result.status}); the specification requires 403",
            evidence,
            SPEC_TRANSPORT_SECURITY,
        )
    if result.status == 403:
        return CheckResult("origin-header", "Origin header validated", PASS, "foreign Origin rejected with HTTP 403", evidence, SPEC_TRANSPORT_SECURITY)
    if 400 <= result.status < 500:
        return CheckResult(
            "origin-header",
            "Origin header validated",
            PASS,
            f"foreign Origin rejected with HTTP {result.status} (the specification says 403)",
            evidence,
            SPEC_TRANSPORT_SECURITY,
        )
    return CheckResult(
        "origin-header", "Origin header validated", INCONCLUSIVE, f"HTTP {result.status} is neither acceptance nor a client error", evidence
    )


def check_null_origin(client: httpx.Client, url: str, lifecycle: Lifecycle) -> CheckResult:
    """``Origin: null`` is what a sandboxed page or a redirect chain sends. The specification does
    not say, and the SDKs disagree, so this is reported and never scored."""
    try:
        result = _send_opener(client, url, lifecycle, {"Origin": "null"})
    except httpx.HTTPError as exc:
        return CheckResult("origin-null", "Origin: null handling", INFO, type(exc).__name__, {})
    if _accepted(result):
        _cleanup_session(client, url, lifecycle, result)
        detail = f"Origin: null accepted (HTTP {result.status})"
    else:
        detail = f"Origin: null rejected (HTTP {result.status})"
    return CheckResult("origin-null", "Origin: null handling", INFO, detail, {"response": result.summary()}, SPEC_TRANSPORT_SECURITY)


def check_session_binding(client: httpx.Client, url: str, lifecycle: Lifecycle) -> CheckResult:
    title = "Session ids bound and terminated"
    if lifecycle.stateless:
        return CheckResult("session-binding", title, SKIP, "no sessions in the stateless lifecycle", {}, SPEC_SESSIONS)
    if not lifecycle.session_id:
        return CheckResult("session-binding", title, SKIP, "the server issued no Mcp-Session-Id", {}, SPEC_SESSIONS)

    evidence: Dict[str, Any] = {}
    failures: List[str] = []

    fabricated = f"mcp-ssrf-check-{_nonce()}"
    try:
        guessed = post(client, url, "tools/list", {}, lifecycle, session_id=fabricated)
    except httpx.HTTPError as exc:
        return CheckResult("session-binding", title, INCONCLUSIVE, type(exc).__name__, evidence, SPEC_SESSIONS)
    evidence["fabricated_session"] = guessed.summary()
    if _accepted(guessed):
        failures.append(f"a fabricated session id was honoured (HTTP {guessed.status})")

    try:
        second = open_lifecycle(client, url, preferred="stateful")
    except Exception as exc:  # TargetUnreachable or transport error: report, do not guess
        evidence["second_session"] = f"could not open: {type(exc).__name__}"
        second = None
    if second is not None and second.session_id:
        try:
            delete_status = delete_session(client, url, second, second.session_id)
        except httpx.HTTPError as exc:
            delete_status = None
            evidence["delete"] = type(exc).__name__
        if delete_status is not None:
            evidence["delete"] = {"status": delete_status}
        if delete_status is not None and delete_status < 400:
            try:
                after = post(client, url, "tools/list", {}, lifecycle, session_id=second.session_id)
            except httpx.HTTPError as exc:
                return CheckResult("session-binding", title, INCONCLUSIVE, type(exc).__name__, evidence, SPEC_SESSIONS)
            evidence["terminated_session"] = after.summary()
            if _accepted(after):
                failures.append(f"a terminated session id was still honoured (HTTP {after.status}); the specification requires 404")
        elif delete_status == 405:
            evidence["terminated_session"] = "not exercised: the server does not support DELETE"
    elif second is not None:
        evidence["second_session"] = "the second initialize returned no session id"

    if failures:
        return CheckResult("session-binding", title, FAIL, "; ".join(failures), evidence, SPEC_SESSIONS)
    return CheckResult("session-binding", title, PASS, "fabricated and terminated session ids are refused", evidence, SPEC_SESSIONS)


def _classify(result, reached: bool) -> str:
    if reached:
        return "reached"
    if result.jsonrpc_error is not None:
        return "refused"
    if result.has_result and isinstance(result.message, dict) and result.message.get("result", {}).get("isError"):
        return "refused"
    if not (200 <= result.status < 300):
        return "refused"
    return "unclear"


def _redirect_probe(call, listener: CallbackListener, nonce: str, callback_host: str, redirect_target: str, settle: float) -> Dict[str, Any]:
    port = listener.port
    # A target the server fetches when asked directly proves nothing about redirects.
    direct_token = f"{nonce}-redirect-target"
    listener.expect(direct_token)
    try:
        direct = call(f"http://{redirect_target}:{port}/hit/{direct_token}")
    except httpx.HTTPError as exc:
        return {"outcome": "transport-error", "error": type(exc).__name__}
    if listener.wait_for(direct_token, settle):
        return {"outcome": "not-exercised", "note": f"the server fetches {redirect_target} when asked directly", "response": direct.summary()}
    token = f"{nonce}-redirect"
    listener.expect(token)
    try:
        result = call(f"http://{callback_host}:{port}/redirect/{token}")
    except httpx.HTTPError as exc:
        return {"outcome": "transport-error", "error": type(exc).__name__}
    first_hop = listener.wait_for(token, settle)
    if not first_hop:
        return {"outcome": "not-exercised", "note": f"the first hop on {callback_host} never arrived", "response": result.summary()}
    if listener.wait_for(f"{token}-redirected", settle):
        return {"outcome": "reached", "target": redirect_target, "response": result.summary(), "peers": listener.peers(f"{token}-redirected")}
    return {"outcome": "refused", "target": redirect_target, "response": result.summary()}


def _tool_wiring(client: httpx.Client, url: str, lifecycle: Lifecycle, *, tool: str, argument: str):
    """Check --fetch-tool and --url-argument against ``tools/list`` before probing.

    Returns ``None`` when the tool and argument are listed, or when the server does not
    answer ``tools/list`` with a result (then the probes run as before and a -32602 on
    ``tools/call`` is still recognised). Otherwise returns ``(detail, evidence)`` for an
    INCONCLUSIVE verdict: a check that never reached the tool must not read as a pass.
    """
    try:
        listing = post(client, url, "tools/list", {}, lifecycle, req_id=6)
    except httpx.HTTPError:
        return None
    if not listing.has_result or not isinstance(listing.message, dict):
        return None
    tools = listing.message.get("result", {}).get("tools")
    if not isinstance(tools, list):
        return None
    names = sorted(str(t.get("name")) for t in tools if isinstance(t, dict) and t.get("name"))
    evidence = {"tool": tool, "argument": argument, "tools_listed": names}
    if tool not in names:
        listed = ", ".join(names) if names else "nothing"
        return f"tools/list has no tool named {tool!r} (it lists: {listed}); check --fetch-tool", evidence
    schema = next((t.get("inputSchema") for t in tools if isinstance(t, dict) and t.get("name") == tool), None)
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if isinstance(properties, dict) and properties and argument not in properties:
        evidence["arguments_listed"] = sorted(properties)
        return f"tool {tool!r} takes {', '.join(sorted(properties))}, not {argument!r}; check --url-argument", evidence
    return None


def check_tool_url_ssrf(
    client: httpx.Client,
    url: str,
    lifecycle: Lifecycle,
    *,
    tool: str,
    argument: str,
    listener: CallbackListener,
    callback_host: str = "127.0.0.1",
    redirect_target: Optional[str] = None,
    control_url: Optional[str] = None,
    settle: float = 0.5,
) -> List[CheckResult]:
    """Ask the named tool to fetch the listener by every loopback spelling, and see what arrives.

    The redirect probe runs only when ``redirect_target`` is given: the listener answers the
    first hop on ``callback_host`` with a 302 to ``redirect_target``. It shows something only
    when the server allows the first hop and refuses the target when asked for it directly,
    so both are checked and the probe is reported as not exercised otherwise.
    """
    results: List[CheckResult] = []
    nonce = _nonce()
    port = listener.port
    title = "URL-fetching tool refuses loopback"

    # The official SDK answers `tools/call` for a tool it does not have with an ordinary
    # result carrying `isError: true`, not a JSON-RPC -32602. On the wire that is exactly
    # what a tool that refused the URL looks like, so a typo in --fetch-tool used to
    # produce PASS ("every loopback spelling was refused"). Ask the server what it has first.
    wiring = _tool_wiring(client, url, lifecycle, tool=tool, argument=argument)
    if wiring is not None:
        results.append(CheckResult("tool-url-ssrf", title, INCONCLUSIVE, wiring[0], wiring[1], SBP_SSRF))
        return results

    def call(target: str):
        return post(client, url, "tools/call", {"name": tool, "arguments": {argument: target}}, lifecycle, req_id=7)

    if control_url:
        try:
            control = call(control_url)
        except httpx.HTTPError as exc:
            results.append(CheckResult("tool-wiring", "Tool fetches a permitted URL", INCONCLUSIVE, type(exc).__name__, {}))
        else:
            ok = _classify(control, reached=False) == "unclear"  # a plain result with no error is what success looks like
            results.append(
                CheckResult(
                    "tool-wiring",
                    "Tool fetches a permitted URL",
                    PASS if ok else INCONCLUSIVE,
                    "the control URL was fetched without error" if ok else f"the control URL produced {_classify(control, False)}; the tool or argument name may be wrong",
                    {"response": control.summary()},
                )
            )

    matrix: Dict[str, Dict[str, Any]] = {}
    reached: List[str] = []
    unclear: List[str] = []
    wiring_error: Optional[str] = None

    for label, host in LOOPBACK_SPELLINGS:
        token = f"{nonce}-{label}"
        listener.expect(token)
        probe = f"http://{host}:{port}/hit/{token}"
        try:
            result = call(probe)
        except httpx.HTTPError as exc:
            matrix[label] = {"outcome": "transport-error", "error": type(exc).__name__}
            continue
        arrived = listener.wait_for(token, settle)
        outcome = _classify(result, arrived)
        entry: Dict[str, Any] = {"outcome": outcome, "response": result.summary()}
        if arrived:
            entry["peers"] = listener.peers(token)
            reached.append(label)
        elif outcome == "unclear":
            unclear.append(label)
        err = result.jsonrpc_error
        if err is not None and err.get("code") in (JSONRPC_INVALID_PARAMS, JSONRPC_METHOD_NOT_FOUND):
            wiring_error = f"JSON-RPC {err.get('code')} on tools/call: check --fetch-tool and --url-argument"
        matrix[label] = entry

    if redirect_target is None:
        matrix["redirect"] = {"outcome": "not-requested", "note": "pass --redirect-target to probe redirect following"}
    else:
        matrix["redirect"] = _redirect_probe(call, listener, nonce, callback_host, redirect_target, settle)
        if matrix["redirect"]["outcome"] == "reached":
            reached.append("redirect")

    evidence = {
        "tool": tool,
        "argument": argument,
        "listener": {"host": listener.host, "port": port, "ipv6": listener.ipv6_bound, "redirect_host": listener.redirect_host},
        "spellings": matrix,
    }
    if reached:
        detail = f"the tool fetched the checker's listener via: {', '.join(reached)}"
        results.append(CheckResult("tool-url-ssrf", title, FAIL, detail, evidence, SBP_SSRF))
    elif wiring_error:
        results.append(CheckResult("tool-url-ssrf", title, INCONCLUSIVE, wiring_error, evidence, SBP_SSRF))
    elif unclear:
        detail = (
            f"nothing reached the listener, but the tool answered without an error for: {', '.join(unclear)}. "
            "Either it did not fetch, or it fetched from a network namespace the listener is not in."
        )
        results.append(CheckResult("tool-url-ssrf", title, INCONCLUSIVE, detail, evidence, SBP_SSRF))
    else:
        results.append(CheckResult("tool-url-ssrf", title, PASS, "every loopback spelling was refused and nothing reached the listener", evidence, SBP_SSRF))
    return results
