"""SSRF / DNS-Rebinding Egress Firewall.

Evaluates URLs pulled out of outbound MCP `tools/call` arguments against a
per-virtual-key egress policy -- the same `dict` shape `BasePolicyResolver.resolve_policy()`
already returns for `allowed_tools`/`blocked_tools` (see `tool_rbac.py`), extended with
three optional keys:

    {
        "egress_mode": "DEFAULT_BLOCK" | "ALLOWLIST_ONLY",   # default: DEFAULT_BLOCK
        "allowed_domains": ["*.internal.corp", "api.github.com"],
        "additional_denied_cidrs": ["10.50.0.0/16"],
    }

DNS Rebinding Protection: every A/AAAA record returned for a hostname is checked, not
just the first -- a resolver that answers with one public IP and one
`169.254.169.254` is rejected on the strength of the second record alone.

Fail-Closed: DNS resolution failures, timeouts, and empty answers are treated as
violations. There is no code path that lets an unresolved or ambiguous hostname
through.
"""

from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Optional, Sequence, Union
from urllib.parse import urlsplit

IPNetwork = Union["ipaddress.IPv4Network", "ipaddress.IPv6Network"]
IPAddress = Union["ipaddress.IPv4Address", "ipaddress.IPv6Address"]
AsyncResolver = Callable[[str], Awaitable[List[str]]]

# Baseline denylist: RFC 1918 private space, loopback, link-local / cloud metadata
# (169.254.169.254 lives here), CGNAT, IETF special-purpose/documentation ranges, and
# their IPv6 equivalents. Applied on every policy regardless of mode -- there is no
# override that re-opens these ranges, only `additional_denied_cidrs` to add more.
BASELINE_DENIED_CIDRS: Sequence[str] = (
    "0.0.0.0/8",  # "this" network
    "10.0.0.0/8",  # RFC 1918
    "100.64.0.0/10",  # carrier-grade NAT (RFC 6598)
    "127.0.0.0/8",  # loopback
    "169.254.0.0/16",  # link-local, incl. 169.254.169.254 cloud metadata
    "172.16.0.0/12",  # RFC 1918
    "192.0.0.0/24",  # IETF protocol assignments
    "192.0.2.0/24",  # TEST-NET-1
    "192.168.0.0/16",  # RFC 1918
    "198.18.0.0/15",  # benchmarking
    "198.51.100.0/24",  # TEST-NET-2
    "203.0.113.0/24",  # TEST-NET-3
    "224.0.0.0/4",  # multicast
    "240.0.0.0/4",  # reserved
    "255.255.255.255/32",  # limited broadcast
    "::/128",  # unspecified
    "::1/128",  # loopback
    "64:ff9b::/96",  # NAT64 well-known prefix (can tunnel an IPv4 target above)
    "100::/64",  # discard-only
    "2001:db8::/32",  # documentation
    "fc00::/7",  # unique local
    "fe80::/10",  # link-local
    "ff00::/8",  # multicast
)

DEFAULT_DNS_TIMEOUT_SECONDS = 2.0
MAX_WALK_DEPTH = 20

_URL_FINDER = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


class EgressPolicyViolationError(Exception):
    """Raised when a URL argument names or resolves to a forbidden egress destination."""

    def __init__(self, url: str, host: str, reason: str, matched_ip: Optional[str] = None, matched_rule: Optional[str] = None):
        self.url = url
        self.host = host
        self.reason = reason
        self.matched_ip = matched_ip
        self.matched_rule = matched_rule
        super().__init__(f"Egress blocked for host {host!r} ({reason})")


@dataclass(frozen=True)
class CompiledEgressPolicy:
    mode: str
    denied_networks: tuple
    allowed_domains: tuple


def compile_policy(policy: Optional[dict]) -> CompiledEgressPolicy:
    """Compiles a raw policy dict (from `resolve_policy()`) into checkable form.

    Missing/absent keys fall back to safe defaults: DEFAULT_BLOCK mode (any host
    permitted unless its resolved IP lands in a denied CIDR) with just the baseline
    denylist -- i.e. a caller that supplies no egress policy still gets SSRF/metadata
    protection for free.
    """
    policy = policy or {}
    mode = policy.get("egress_mode", "DEFAULT_BLOCK")
    if mode not in ("DEFAULT_BLOCK", "ALLOWLIST_ONLY"):
        mode = "DEFAULT_BLOCK"

    extra_cidrs = policy.get("additional_denied_cidrs") or []
    networks = tuple(
        ipaddress.ip_network(cidr, strict=False) for cidr in (*BASELINE_DENIED_CIDRS, *extra_cidrs)
    )
    allowed_domains = tuple(str(d).lower() for d in (policy.get("allowed_domains") or []))
    return CompiledEgressPolicy(mode=mode, denied_networks=networks, allowed_domains=allowed_domains)


def _domain_allowed(host: str, allowed_domains: Sequence[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(fnmatch.fnmatchcase(host, pattern) for pattern in allowed_domains)


def _literal_ip(host: str) -> Optional[IPAddress]:
    """Parses `host` as an IP literal (bracketed IPv6 included), or returns None."""
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def _normalize(ip: IPAddress) -> IPAddress:
    """Unwraps an IPv4-mapped IPv6 address (::ffff:a.b.c.d) to its embedded IPv4 form
    so CIDR matching can't be bypassed by asking the target to answer in that shape."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def _blocking_network(ip_str: str, denied_networks: Sequence[IPNetwork]) -> Optional[IPNetwork]:
    try:
        ip = _normalize(ipaddress.ip_address(ip_str))
    except ValueError:
        return None
    for net in denied_networks:
        if ip.version == net.version and ip in net:
            return net
    return None


def extract_host(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise EgressPolicyViolationError(url=url, host="", reason="unsupported_scheme")
    host = parsed.hostname
    if not host:
        raise EgressPolicyViolationError(url=url, host="", reason="missing_host")
    return host


async def _default_resolve(host: str) -> List[str]:
    """Resolves every A/AAAA record for `host` via the stdlib async resolver.

    `getaddrinfo` is blocking C code, so it runs on the default executor. The caller
    (`evaluate_url`) wraps this in `wait_for` -- timeout enforcement lives in one
    place so it applies uniformly to the default resolver and any injected one.
    """
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


async def evaluate_url(
    url: str,
    policy: Optional[dict] = None,
    *,
    resolver: Optional[AsyncResolver] = None,
    timeout: float = DEFAULT_DNS_TIMEOUT_SECONDS,
) -> None:
    """Fail-closed egress check for a single URL. Raises `EgressPolicyViolationError` on any violation.

    `resolver` is injectable (async `host -> [ip, ...]`) so tests -- and DNS-rebinding
    simulations in particular -- can control exactly what a hostname "resolves" to
    without touching a real network.
    """
    compiled = compile_policy(policy)
    host = extract_host(url)

    if compiled.mode == "ALLOWLIST_ONLY" and not _domain_allowed(host, compiled.allowed_domains):
        raise EgressPolicyViolationError(url=url, host=host, reason="allowlist_only_denied")

    literal_ip = _literal_ip(host)
    if literal_ip is not None:
        candidate_ips = [str(literal_ip)]
    else:
        resolve = resolver or _default_resolve
        try:
            candidate_ips = await asyncio.wait_for(resolve(host), timeout=timeout)
        except EgressPolicyViolationError:
            raise
        except Exception as exc:
            # Fail-closed: DNS timeout, NXDOMAIN, or any resolver error (default or
            # injected) blocks the request rather than letting an unresolved host through.
            raise EgressPolicyViolationError(url=url, host=host, reason=f"dns_resolution_failed: {exc}") from exc
        if not candidate_ips:
            raise EgressPolicyViolationError(url=url, host=host, reason="dns_resolution_empty")

    # DNS Rebinding Protection: inspect every resolved IP, not just the first -- a
    # rebinding attacker only needs one record to point at a blocked target.
    for ip_str in candidate_ips:
        blocked_net = _blocking_network(ip_str, compiled.denied_networks)
        if blocked_net is not None:
            raise EgressPolicyViolationError(
                url=url,
                host=host,
                reason="ip_in_denied_cidr",
                matched_ip=ip_str,
                matched_rule=str(blocked_net),
            )


def find_urls(value: Any, *, max_depth: int = MAX_WALK_DEPTH) -> List[str]:
    """Recursively AST-walks arbitrary JSON-RPC arguments, collecting http(s) URL substrings.

    Traversal silently stops past `max_depth` rather than raising: a pathologically
    nested payload is a job for the router's own payload-depth guard, not this scan.
    """
    found: List[str] = []
    seen: set = set()

    def _walk(v: Any, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(v, str):
            for match in _URL_FINDER.findall(v):
                if match not in seen:
                    seen.add(match)
                    found.append(match)
        elif isinstance(v, dict):
            for item in v.values():
                _walk(item, depth + 1)
        elif isinstance(v, (list, tuple)):
            for item in v:
                _walk(item, depth + 1)

    _walk(value, 0)
    return found


async def scan_arguments(
    arguments: Any,
    policy: Optional[dict] = None,
    *,
    resolver: Optional[AsyncResolver] = None,
    timeout: float = DEFAULT_DNS_TIMEOUT_SECONDS,
) -> None:
    """Finds every http(s) URL anywhere inside `arguments` and evaluates each one.

    Raises `EgressPolicyViolationError` on the first violation found. Intended as the
    single entry point the MCP router calls before proxying a `tools/call` request.
    """
    for url in find_urls(arguments):
        await evaluate_url(url, policy, resolver=resolver, timeout=timeout)
