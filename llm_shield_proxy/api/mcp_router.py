"""Model Context Protocol (MCP) JSON-RPC 2.0 Gateway Router.

Terminates JSON-RPC 2.0 traffic bound for downstream MCP tool servers, enforcing
Virtual Key RBAC (fail-closed), 3-Tier PII/secret sanitization of tool arguments
and tool outputs, and dynamic tools/list catalog pruning. Supports both single
requests and JSON-RPC 2.0 batch arrays per spec.

Callers authenticate exactly as on the guard API: the X-Shield-Virtual-Key (or Bearer)
value must match VALID_VIRTUAL_KEYS, else the request is refused with HTTP 401 before
the body is read. OVERRIDE_CLIENT_AUTH=true admits unmatched callers as "anonymous"
(policy looked up as "anonymous", X-Shield-Upstream-URL ignored).

Policy keys honoured here, beyond `allowed_tools` / `blocked_tools` and the egress keys
documented in `security/egress_guard.py`:

    "allowed_resources": ["file:///docs/*", "https://kb.example.com/*"]

`resources/read` is default-deny: the requested `uri` must match one of these
`fnmatch` patterns (case-sensitive; `*` also matches `/`), compared against the URI
percent-decoded until stable. A URI with a `.` or `..` segment (in any encoding), an
encoded `/` or `\\`, or a backslash is always refused. A fail-closed policy
(`_FAIL_CLOSED_` sentinel from a resolver failure or an empty allowlist under
MCP_EMPTY_ALLOWLIST_MODE=DENY_ALL) refuses every method, including tools/list and resources/read, before any upstream call.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hmac
import inspect
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx
import orjson
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response

from llm_shield_proxy.engines.masking import ScrubVault
from llm_shield_proxy.engines.pii_engine import pii_engine
from llm_shield_proxy.engines.vault import Vault
from llm_shield_proxy.observability.audit import AuditLogger
from llm_shield_proxy.security.egress_guard import (
    EgressPolicyViolationError,
    PinnedTarget,
    evaluate_url,
    find_urls,
    resolve_pinned_target,
    scan_arguments,
)
from llm_shield_proxy.security.key_fingerprint import get_virtual_key_id
from llm_shield_proxy.security.tool_rbac import BasePolicyResolver, build_policy_resolver

logger = logging.getLogger(__name__)


def _redact_url_for_audit(url: str) -> str:
    """Reduce a URL to scheme and authority for the signed audit chain.

    The PATH goes too, not just the query, fragment and userinfo. A blocked `tools/call`
    URL is attacker-shaped and identifiers sit in paths as readily as in query params --
    `https://x.test/customer/123-45-6789` would otherwise be written verbatim into a
    tamper-evident record that is meant to contain no raw request content. Nothing is
    lost for forensics: the audit entry already carries `blocked_host`, `resolved_ip` and
    `matched_rule` as their own fields.

    Fails soft, and that is the point: this runs INSIDE the egress-violation handler, so
    an exception here would replace a security rejection with a 500 and lose the audit
    event entirely. `urlsplit(...).port` raises ValueError on a malformed or out-of-range
    port, which an attacker controls, so a URL that cannot be parsed is recorded as
    unparseable rather than allowed to abort the denial.
    """
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        host = f"[{hostname}]" if ":" in hostname else hostname
        if parsed.port is not None:
            host += f":{parsed.port}"
        return urlunsplit((parsed.scheme, host, "", "", ""))
    except ValueError:
        return "<unparseable-url>"


mcp_router = APIRouter()

SUPPORTED_METHODS = {"tools/call", "tools/list", "resources/read"}

# JSON-RPC 2.0 reserved / gateway-specific error codes
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_UPSTREAM_ERROR = -32000
JSONRPC_UNAUTHORIZED = -32001
JSONRPC_TOOL_FORBIDDEN = -32003
# Same reserved code as JSONRPC_TOOL_FORBIDDEN (both are server-error range policy
# denials); named separately so call sites read as what they actually reject.
JSONRPC_EGRESS_FORBIDDEN = -32003

MAX_SANITIZE_DEPTH = 20
MAX_BATCH_SIZE = 100

# Sentinel `_resolve_role` / the RBAC resolvers put in allowed_tools when the caller
# must be denied everything. It is never a grantable tool name.
FAIL_CLOSED_SENTINEL = "_FAIL_CLOSED_"

# A `..` path segment, literal or percent-encoded, with slash or backslash separators.
_DOT_DOT_SEGMENT = re.compile(r"(?:^|[/\\])(?:\.|%2e){2}(?:[/\\?#]|$)", re.IGNORECASE)
# A percent-encoded separator: `..%2f` is a traversal to a server that decodes it.
_ENCODED_SEPARATOR = re.compile(r"%(?:2f|5c)", re.IGNORECASE)
# Decoding rounds before a URI that keeps changing is refused (`%252e` is two rounds).
_MAX_URI_DECODE_ROUNDS = 4


def _get_mcp_policy_resolver_for_app(app: Any) -> BasePolicyResolver:
    """Build the configured MCP resolver against application-scoped state."""
    if not hasattr(app.state, "mcp_rbac_state"):
        from collections import OrderedDict

        from llm_shield_proxy.security.tool_rbac import BoundedLockMap

        app.state.mcp_rbac_state = {
            "cache": OrderedDict(),
            "cache_lock": asyncio.Lock(),
            "inflight_locks": BoundedLockMap(maxsize=1000),
            "background_tasks": set(),
        }
    shared_state = app.state.mcp_rbac_state

    from llm_shield_proxy.engines.vault import vault_store

    http_client = getattr(app.state, "http_client", None) or httpx.AsyncClient()
    redis_client = vault_store.async_client if hasattr(vault_store, "async_client") else None
    return build_policy_resolver(http_client, shared_state, redis_client)


async def get_mcp_policy_resolver(request: Request) -> BasePolicyResolver:
    """Dependency injection provider for the active Pluggable RBAC Engine (MCP scope)."""
    return _get_mcp_policy_resolver_for_app(request.app)


def _warn_for_empty_allowlist(policy: Dict[str, Any], mode: str, *, phase: str) -> None:
    """Make an empty MCP allowlist visible without conflating its two supported semantics."""
    if policy.get("allowed_tools"):
        return

    if mode == "BLOCKLIST_ONLY":
        logger.critical(
            "SECURITY RISK: the MCP route is enabled and the %s policy has no allowed_tools entries. "
            "MCP_EMPTY_ALLOWLIST_MODE=BLOCKLIST_ONLY permits every tool not explicitly named in "
            "blocked_tools. Use DENY_ALL or configure an allowlist unless this permissive policy is intentional.",
            phase,
        )
        return

    logger.warning(
        "The MCP route is enabled and the %s policy has no allowed_tools entries. "
        "MCP_EMPTY_ALLOWLIST_MODE=DENY_ALL is fail-closed, so every tools/call request will be denied "
        "until an allowlist is configured.",
        phase,
    )


async def warn_if_mcp_policy_is_empty_at_startup(app: Any) -> None:
    """Resolve one startup policy and loudly report an empty MCP allowlist."""
    from llm_shield_proxy.core.config import settings

    provider = app.dependency_overrides.get(get_mcp_policy_resolver)
    try:
        if provider is None:
            resolver = _get_mcp_policy_resolver_for_app(app)
        else:
            resolver = provider()
            if inspect.isawaitable(resolver):
                resolver = await resolver
        policy = await resolver.resolve_policy("__mcp_startup_probe__")
    except Exception as exc:
        logger.warning(
            "The MCP route is enabled, but its resolver could not be inspected at startup (%s). "
            "Verify the deployed resolver's empty-policy and failure behavior before routing tool calls.",
            type(exc).__name__,
        )
        return

    _warn_for_empty_allowlist(policy, settings.MCP_EMPTY_ALLOWLIST_MODE, phase="startup resolver")


def _extract_virtual_key(
    x_shield_virtual_key: Optional[str], authorization: Optional[str]
) -> str:
    """Extracts caller identity from X-Shield-Virtual-Key or Authorization Bearer header."""
    if x_shield_virtual_key and x_shield_virtual_key.strip():
        return x_shield_virtual_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            return token
    return "anonymous"


def _authenticate_caller(
    x_shield_virtual_key: Optional[str], authorization: Optional[str]
) -> Tuple[Optional[str], bool]:
    """Fail-closed authentication against VALID_VIRTUAL_KEYS, mirroring the guard API.

    Returns ``(policy_key, authenticated)``. ``policy_key`` is the MATCHED configured
    key (the existing Redis/OPA/Vault policy stores are keyed by it), "anonymous" when
    OVERRIDE_CLIENT_AUTH admits an unmatched caller, or None when the caller must be
    refused. A caller-asserted string is never used as an identity on its own.
    """
    from llm_shield_proxy.core.config import settings

    presented = _extract_virtual_key(x_shield_virtual_key, authorization)
    valid_keys = settings.valid_virtual_keys_set
    if valid_keys and presented != "anonymous":
        presented_bytes = presented.encode("utf-8")
        matched: Optional[str] = None
        # Compare as bytes: compare_digest rejects non-ASCII str with TypeError (a 500).
        # No early exit, so timing does not reveal which configured key was close.
        for vk in valid_keys:
            if hmac.compare_digest(presented_bytes, vk.encode("utf-8")):
                matched = vk
        if matched is not None:
            return matched, True
    if settings.OVERRIDE_CLIENT_AUTH:
        return "anonymous", False
    return None, False


def _resolve_upstream_url(x_shield_upstream_url: Optional[str], *, authenticated: bool = True) -> Optional[str]:
    """Resolves upstream MCP target from X-Shield-Upstream-URL or UPSTREAM_MCP_BASE_URL env var.

    Only an authenticated caller may redirect the gateway with the header (the target is
    still egress-checked); an anonymous OVERRIDE_CLIENT_AUTH caller gets the operator's.
    """
    if authenticated and x_shield_upstream_url and x_shield_upstream_url.strip():
        return x_shield_upstream_url.strip()
    return os.environ.get("UPSTREAM_MCP_BASE_URL")


class _PayloadTooLarge(Exception):
    pass


async def _read_body_capped(request: Request, limit: int) -> bytes:
    """Reads the body enforcing `limit` bytes (Content-Length pre-check + streaming total).

    Local equivalent of main.read_body_with_limit (importing main here would be
    circular). Raises _PayloadTooLarge; a bad Content-Length raises ValueError.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        declared_length = int(declared)  # ValueError on garbage -> caller's 400
        if declared_length < 0:
            raise ValueError("negative Content-Length")
        if declared_length > limit:
            raise _PayloadTooLarge()

    body = bytearray()
    stream_iter = request.stream()
    while True:
        try:
            # Per-chunk deadline, as on the proxy path, so a trickled body cannot pin the worker.
            async with asyncio.timeout(5.0):
                chunk = await anext(stream_iter)
        except StopAsyncIteration:
            break
        if len(body) + len(chunk) > limit:
            raise _PayloadTooLarge()
        body.extend(chunk)
    return bytes(body)


def _is_fail_closed(allowed: set) -> bool:
    return FAIL_CLOSED_SENTINEL in allowed


def _normalized_resource_uri(uri: str) -> Optional[str]:
    """The fully percent-decoded URI to match patterns against, or None to refuse it.

    The URI is decoded until it stops changing, because an upstream may decode once or
    several times (`%252e%252e` -> `%2e%2e` -> `..`). Refused: an encoded `/` or `\\` at
    any round, a backslash or control character in the decoded form, any `.` or `..`
    segment, and a URI still changing after _MAX_URI_DECODE_ROUNDS.
    """
    current = uri
    for _ in range(_MAX_URI_DECODE_ROUNDS + 1):
        if _ENCODED_SEPARATOR.search(current):
            return None
        decoded = unquote(current, errors="strict")
        if decoded == current:
            break
        current = decoded
    else:
        return None
    if "\\" in current or any(ord(char) < 0x20 or ord(char) == 0x7F for char in current):
        return None
    if any(segment in (".", "..") for segment in current.split("/")):
        return None
    return current


def _is_resource_forbidden(uri: Any, policy: Dict[str, Any]) -> bool:
    """Default-deny check for resources/read against the policy's `allowed_resources` patterns."""
    if not isinstance(uri, str) or not uri:
        return True
    if _DOT_DOT_SEGMENT.search(uri):
        return True
    try:
        normalized = _normalized_resource_uri(uri)
    except UnicodeDecodeError:
        return True  # an escape that is not UTF-8
    if normalized is None:
        return True
    patterns = policy.get("allowed_resources") or []
    if not isinstance(patterns, (list, tuple)):
        return True
    return not any(isinstance(p, str) and p and fnmatch.fnmatchcase(normalized, p) for p in patterns)


def _check_depth(value: Any, depth: int = 0) -> None:
    """Raises ValueError when `value` nests deeper than MAX_SANITIZE_DEPTH (same rule as _sanitize_json)."""
    if depth > MAX_SANITIZE_DEPTH:
        raise ValueError("Maximum payload nesting depth exceeded")
    if isinstance(value, dict):
        for v in value.values():
            _check_depth(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _check_depth(v, depth + 1)


def _is_tool_forbidden(tool_name: str, allowed: set, blocked: set) -> bool:
    # The fail-closed sentinel denies everything; it must not act as a grant for a
    # tool that happens to be named "_FAIL_CLOSED_".
    if FAIL_CLOSED_SENTINEL in allowed:
        return True
    if tool_name in blocked:
        return True
    if allowed and tool_name not in allowed:
        return True
    return False


def _sanitize_json(value: Any, vault: Any, profile: Any, depth: int = 0) -> Any:
    """Recursively AST-walks arbitrary JSON-RPC params/results through the 3-Tier PII/secret cascade."""
    if depth > MAX_SANITIZE_DEPTH:
        raise ValueError("Maximum payload nesting depth exceeded")

    if isinstance(value, str):
        return pii_engine.redact_text(value, vault, profile)
    if isinstance(value, dict):
        return {k: _sanitize_json(v, vault, profile, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_json(v, vault, profile, depth + 1) for v in value]
    return value


async def _sanitize_async(value: Any, vault: Any, profile: Any) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sanitize_json, value, vault, profile)


def _prune_tools(tools: List[Dict[str, Any]], allowed: set, blocked: set) -> List[Dict[str, Any]]:
    """Prunes tool definitions not permitted for the active Virtual Key (O(1) membership lookups).

    Only the `tools` array is replaced; sibling keys on the result object (e.g. a
    `nextCursor` pagination token) are left untouched so client-side pagination
    state is never corrupted by RBAC filtering.
    """
    pruned = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not name:
            continue
        if _is_tool_forbidden(name, allowed, blocked):
            continue
        pruned.append(tool)
    return pruned


def _jsonrpc_error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _jsonrpc_error_response(req_id: Any, code: int, message: str, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=_jsonrpc_error(req_id, code, message))


async def _process_single_call(
    item: Any,
    allowed: set,
    blocked: set,
    virtual_key: str,
    upstream: Optional[PinnedTarget],
    http_client: httpx.AsyncClient,
    egress_policy: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Processes one JSON-RPC 2.0 request object (batch item or the sole top-level request).

    Returns the JSON-RPC response object, or None if `item` is a notification
    (no `id` member) — per spec, notifications never receive a response.
    """
    has_id = isinstance(item, dict) and "id" in item
    req_id = item.get("id") if isinstance(item, dict) else None

    if not isinstance(item, dict) or item.get("jsonrpc") != "2.0" or "method" not in item:
        return _jsonrpc_error(req_id, JSONRPC_INVALID_REQUEST, "Invalid Request: malformed JSON-RPC 2.0 envelope") if has_id else None

    method = item.get("method")
    params = item.get("params") or {}
    if not isinstance(params, dict):
        return _jsonrpc_error(req_id, JSONRPC_INVALID_REQUEST, "Invalid Request: params must be an object") if has_id else None

    if method not in SUPPORTED_METHODS:
        return _jsonrpc_error(req_id, JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}") if has_id else None

    # Audit records carry only the key fingerprint, never the credential itself.
    audit_id = get_virtual_key_id(virtual_key)

    # Fail-Closed Gate: tool authorization is checked BEFORE any upstream routing,
    # sanitization, or attacker-controlled DNS resolution.
    #
    # The ordering is the point. When the egress scan below sat above this block, a
    # caller whose role forbids the tool could still make the gateway resolve any
    # hostname it put in `params`: an outbound DNS probe performed on behalf of someone
    # not authorised to call anything. Resolution is attacker-directed work, so it
    # happens only once the caller has been allowed to make the call at all.
    tool_name = params.get("name") if method == "tools/call" else None
    if method == "tools/call":
        if not tool_name or _is_tool_forbidden(tool_name, allowed, blocked):
            AuditLogger.log_security_event(
                event_type="mcp_tool_forbidden",
                severity="CRITICAL",
                details={"reason": "Tool forbidden for active role", "tool_name": tool_name, "method": method},
                virtual_key_id=audit_id,
            )
            return _jsonrpc_error(req_id, JSONRPC_TOOL_FORBIDDEN, "Tool forbidden for active role") if has_id else None
    elif _is_fail_closed(allowed):
        # A fail-closed role has no grants at all, so tools/list and resources/read are
        # refused here too rather than reaching the upstream server.
        AuditLogger.log_security_event(
            event_type="mcp_method_forbidden",
            severity="CRITICAL",
            details={"reason": "Fail-closed policy denies every method", "method": method},
            virtual_key_id=audit_id,
        )
        return _jsonrpc_error(req_id, JSONRPC_TOOL_FORBIDDEN, "Forbidden for active role") if has_id else None
    elif method == "resources/read" and _is_resource_forbidden(params.get("uri"), egress_policy):
        AuditLogger.log_security_event(
            event_type="mcp_resource_forbidden",
            severity="CRITICAL",
            # The URI itself is caller-shaped and may carry identifiers; log only its form.
            details={
                "reason": "Resource not in allowed_resources",
                "method": method,
                "resource": _redact_url_for_audit(params.get("uri")) if isinstance(params.get("uri"), str) else None,
            },
            virtual_key_id=audit_id,
        )
        return _jsonrpc_error(req_id, JSONRPC_TOOL_FORBIDDEN, "Resource forbidden for active role") if has_id else None

    # One depth limit over everything that will be forwarded, so the egress scan below
    # can never be cut short by nesting (it fails closed past the same limit anyway).
    try:
        _check_depth(params)
    except ValueError:
        return _jsonrpc_error(req_id, JSONRPC_INVALID_REQUEST, "Payload nesting depth exceeded") if has_id else None

    # SSRF / DNS-rebinding gate, for EVERY supported method rather than only
    # `tools/call`. `resources/read` exists to fetch a URI, and it used to skip this
    # gate entirely: a `resources/read` naming the cloud metadata endpoint was forwarded
    # upstream unchecked. The scan also covers the whole of `params`, not just
    # `params["arguments"]`, because a URL in a sibling field such as `_meta` reaches an
    # upstream tool exactly as readily as one inside `arguments`.
    #
    # Scanning the whole of `params` subsumes `params["arguments"]`, so there is no
    # second pass over the arguments. One used to follow this, purely so the audit
    # record could name the tool; it resolved every argument URL a second time, which
    # doubled outbound DNS on every allowed call. `tool_name` is in this record instead.
    try:
        await scan_arguments(params, egress_policy)
    except EgressPolicyViolationError as exc:
        AuditLogger.log_security_event(
            event_type="mcp_egress_policy_violation",
            severity="CRITICAL",
            details={
                "reason": exc.reason,
                "tool_name": tool_name,
                "method": method,
                "blocked_url": _redact_url_for_audit(exc.url),
                "blocked_host": exc.host,
                "resolved_ip": exc.matched_ip,
                "matched_rule": exc.matched_rule,
                "applied_role_name": egress_policy.get("role_name", audit_id),
            },
            virtual_key_id=audit_id,
        )
        return (
            _jsonrpc_error(
                req_id,
                JSONRPC_EGRESS_FORBIDDEN,
                "SSRF Policy Violation: Target IP/Host forbidden by egress policy",
            )
            if has_id
            else None
        )

    if upstream is None:
        return _jsonrpc_error(req_id, JSONRPC_UPSTREAM_ERROR, "No upstream MCP server configured") if has_id else None

    # Tenant profiles are keyed by the fingerprint, as on every other path.
    active_profile = pii_engine.get_profile(audit_id)

    # Inbound Sanitization: 3-Tier cascade over params before forwarding upstream.
    # Uses Faker-backed synthetic substitution (not a literal "[REDACTED]" marker) so
    # values keep passing strict upstream tool schemas (EmailStr, phone/SSN formats, etc.).
    inbound_vault = Vault(synthetic=True)
    try:
        if method == "tools/call":
            sanitized_arguments = await _sanitize_async(params.get("arguments", {}), inbound_vault, active_profile)
            forward_params: Dict[str, Any] = {**params, "arguments": sanitized_arguments}
        else:
            forward_params = await _sanitize_async(params, inbound_vault, active_profile)
    except ValueError:
        return _jsonrpc_error(req_id, JSONRPC_INVALID_REQUEST, "Payload nesting depth exceeded") if has_id else None

    # SSRF Gate, second half: re-check anything sanitization INTRODUCED.
    #
    # The gate above deliberately ran on the raw arguments, on the reasoning that those
    # are what an upstream tool receives. They are not. What goes upstream is the
    # sanitized copy, and inbound sanitization uses `Vault(synthetic=True)`, which
    # substitutes realistic look-alike values rather than bracketed markers. When PII
    # sits inside a URL's authority the substitution rewrites the host:
    #
    #     checked   https://bob@example.com.attacker.example/x
    #     forwarded https://jacksondaniel@example.net/x
    #
    # A different registrable domain, never resolved, never evaluated, handed to the
    # upstream tool to dial. Both halves are needed: the raw check still catches a
    # forbidden host that sanitization leaves alone.
    #
    # Only the DIFFERENCE is evaluated, so a payload sanitization did not rewrite -- the
    # overwhelming majority -- costs no additional DNS at all.
    try:
        for introduced in set(find_urls(forward_params)) - set(find_urls(params)):
            await evaluate_url(introduced, egress_policy)
    except EgressPolicyViolationError as exc:
        AuditLogger.log_security_event(
            event_type="mcp_egress_policy_violation",
            severity="CRITICAL",
            details={
                "reason": exc.reason,
                "method": method,
                # Names the half that caught it: this URL was not in the client's
                # request, it was produced by redaction, so an operator reading the
                # chain is not left hunting for a host the caller never sent.
                "detected_in": "sanitized_payload",
                "blocked_url": _redact_url_for_audit(exc.url),
                "blocked_host": exc.host,
                "resolved_ip": exc.matched_ip,
                "matched_rule": exc.matched_rule,
                "applied_role_name": egress_policy.get("role_name", audit_id),
            },
            virtual_key_id=audit_id,
        )
        return (
            _jsonrpc_error(
                req_id,
                JSONRPC_EGRESS_FORBIDDEN,
                "SSRF Policy Violation: Target IP/Host forbidden by egress policy",
            )
            if has_id
            else None
        )

    forward_payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": forward_params}

    try:
        # Reuse the app's shared, pooled HTTP/2 client rather than opening a fresh
        # connection per call: under sustained agentic traffic a per-call client
        # here would mean a fresh TCP/TLS handshake for every tool invocation.
        # `upstream.url` holds the IP that cleared the egress check, with the real
        # hostname carried in Host/sni_hostname, so httpx cannot re-resolve the name
        # and land somewhere the policy rejected.
        upstream_res = await http_client.post(
            upstream.url,
            content=orjson.dumps(forward_payload),
            headers={"content-type": "application/json", **upstream.headers},
            extensions=upstream.extensions,
            timeout=30.0,
        )
        upstream_res.raise_for_status()
        upstream_json = orjson.loads(upstream_res.content)
    except Exception as exc:
        AuditLogger.log_security_event(
            event_type="mcp_upstream_failure",
            severity="CRITICAL",
            details={"reason": "MCP Upstream Unavailable", "error_type": type(exc).__name__, "method": method},
            virtual_key_id=audit_id,
        )
        return _jsonrpc_error(req_id, JSONRPC_UPSTREAM_ERROR, "Upstream MCP server unreachable") if has_id else None

    if not isinstance(upstream_json, dict):
        upstream_json = {"jsonrpc": "2.0", "id": req_id, "result": upstream_json}

    # Dynamic Discovery: prune tool definitions not permitted for the active Virtual Key.
    if method == "tools/list":
        result = upstream_json.get("result")
        if isinstance(result, dict) and isinstance(result.get("tools"), list):
            result["tools"] = _prune_tools(result["tools"], allowed, blocked)

    # Outbound Scan: neutralize any leaked secrets/PII in upstream tool outputs before returning
    # to the client. Uses one-way ScrubVault ("[REDACTED]") since this is terminal, human/agent
    # facing text rather than a payload that must satisfy another schema validator.
    # Every upstream-originated member is scrubbed, not only `result`: a tool can put the
    # same data in `error.message` / `error.data` (or an unknown member) with HTTP 200.
    outbound_vault = ScrubVault()
    try:
        for member in list(upstream_json):
            if member in ("jsonrpc", "id") or upstream_json[member] is None:
                continue
            upstream_json[member] = await _sanitize_async(upstream_json[member], outbound_vault, active_profile)
    except ValueError:
        return _jsonrpc_error(req_id, JSONRPC_INVALID_REQUEST, "Upstream payload nesting depth exceeded") if has_id else None

    if not has_id:
        return None
    upstream_json["id"] = req_id
    return upstream_json


async def _resolve_role(policy_resolver: BasePolicyResolver, virtual_key: str) -> Tuple[set, set, Dict[str, Any]]:
    from llm_shield_proxy.core.config import settings

    policy = await policy_resolver.resolve_policy(virtual_key)
    allowed = set(policy.get("allowed_tools", []))
    blocked = set(policy.get("blocked_tools", []))
    if not allowed and settings.MCP_EMPTY_ALLOWLIST_MODE == "DENY_ALL":
        allowed.add(FAIL_CLOSED_SENTINEL)
    return allowed, blocked, policy


@mcp_router.post("/v1/mcp")
async def mcp_gateway(
    request: Request,
    x_shield_virtual_key: Optional[str] = Header(None, alias="X-Shield-Virtual-Key"),
    x_shield_upstream_url: Optional[str] = Header(None, alias="X-Shield-Upstream-URL"),
    authorization: Optional[str] = Header(None),
    policy_resolver: BasePolicyResolver = Depends(get_mcp_policy_resolver),
) -> Response:
    """MCP JSON-RPC 2.0 gateway: RBAC-gated, sanitized transport dispatcher to upstream MCP servers.

    Accepts both a single JSON-RPC 2.0 request object and a batch (array) of request
    objects per spec section 6. Batch items without an `id` are notifications and are
    silently dropped from the response; if a batch yields no responses at all, an empty
    204 is returned rather than an empty JSON array (also per spec).
    """
    from llm_shield_proxy.core.config import settings

    # Authenticate BEFORE reading or parsing the body, so an unauthenticated caller can
    # make the worker do no body buffering, parsing, policy lookup or upstream work.
    virtual_key, authenticated = _authenticate_caller(x_shield_virtual_key, authorization)
    if virtual_key is None:
        return _jsonrpc_error_response(None, JSONRPC_UNAUTHORIZED, "Unauthorized: invalid or missing virtual key", status_code=401)
    audit_id = get_virtual_key_id(virtual_key)

    try:
        raw_body = await _read_body_capped(request, settings.MAX_PAYLOAD_SIZE_BYTES)
    except _PayloadTooLarge:
        return _jsonrpc_error_response(None, JSONRPC_INVALID_REQUEST, "Invalid Request: payload too large", status_code=413)
    except ValueError:
        return _jsonrpc_error_response(None, JSONRPC_INVALID_REQUEST, "Invalid Request: bad Content-Length", status_code=400)
    except TimeoutError:
        return _jsonrpc_error_response(None, JSONRPC_INVALID_REQUEST, "Invalid Request: body read timed out", status_code=408)

    try:
        req_data = orjson.loads(raw_body) if raw_body else {}
    except Exception:
        return _jsonrpc_error_response(None, JSONRPC_PARSE_ERROR, "Parse error: invalid JSON payload")

    if not isinstance(req_data, (dict, list)):
        return _jsonrpc_error_response(None, JSONRPC_INVALID_REQUEST, "Invalid Request: malformed JSON-RPC 2.0 envelope")

    upstream_url = _resolve_upstream_url(x_shield_upstream_url, authenticated=authenticated)
    allowed, blocked, egress_policy = await _resolve_role(policy_resolver, virtual_key)

    # SSRF / DNS-Rebinding Gate: the upstream routing target itself is client-controlled
    # (X-Shield-Upstream-URL / UPSTREAM_MCP_BASE_URL) and is the actual destination of the
    # outbound request below -- it must clear the same egress firewall applied to URLs found
    # inside tool arguments, not just those. Checked once per gateway call since upstream_url
    # is constant across an entire batch.
    #
    # `resolve_pinned_target` both checks and PINS: it returns the validated IP already
    # substituted into the URL. Validating here and then handing the hostname to httpx would
    # let it resolve a second time at connect, so a low-TTL attacker zone could answer public
    # to this check and 169.254.169.254 to the connection. The pin is what closes that window.
    upstream: Optional[PinnedTarget] = None
    if upstream_url:
        try:
            upstream = await resolve_pinned_target(upstream_url, egress_policy)
        except EgressPolicyViolationError as exc:
            AuditLogger.log_security_event(
                event_type="mcp_egress_policy_violation",
                severity="CRITICAL",
                details={
                    "reason": exc.reason,
                    "method": "upstream_routing",
                    "blocked_url": _redact_url_for_audit(exc.url),
                    "blocked_host": exc.host,
                    "resolved_ip": exc.matched_ip,
                    "matched_rule": exc.matched_rule,
                    "applied_role_name": egress_policy.get("role_name", audit_id),
                },
                virtual_key_id=audit_id,
            )
            return _jsonrpc_error_response(
                None, JSONRPC_EGRESS_FORBIDDEN, "SSRF Policy Violation: Upstream target forbidden by egress policy"
            )

    http_client = getattr(request.app.state, "http_client", None)
    if http_client is None:
        # Defensive fallback for contexts without the app lifespan (e.g. isolated unit
        # tests). Cached on app.state so repeated calls still share one pooled client
        # instead of opening a fresh connection per request.
        http_client = httpx.AsyncClient()
        request.app.state.http_client = http_client

    if isinstance(req_data, list):
        if not req_data:
            return _jsonrpc_error_response(None, JSONRPC_INVALID_REQUEST, "Invalid Request: empty batch")
        if len(req_data) > MAX_BATCH_SIZE:
            return _jsonrpc_error_response(
                None, JSONRPC_INVALID_REQUEST, f"Invalid Request: batch exceeds {MAX_BATCH_SIZE} item limit"
            )

        responses = []
        for item in req_data:
            resp = await _process_single_call(item, allowed, blocked, virtual_key, upstream, http_client, egress_policy)
            if resp is not None:
                responses.append(resp)

        if not responses:
            return Response(status_code=204)
        return JSONResponse(status_code=200, content=responses)

    resp = await _process_single_call(req_data, allowed, blocked, virtual_key, upstream, http_client, egress_policy)
    if resp is None:
        return Response(status_code=204)
    return JSONResponse(status_code=200, content=resp)
