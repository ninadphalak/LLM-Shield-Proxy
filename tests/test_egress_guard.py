"""Tests for the SSRF / DNS-rebinding egress firewall (llm_shield_proxy.security.egress_guard)."""

import pytest

from llm_shield_proxy.security.egress_guard import (
    EgressPolicyViolationError,
    compile_policy,
    evaluate_url,
    find_urls,
    scan_arguments,
)


def _resolver(mapping: dict):
    """Builds an async resolver stub: host -> list[ip]. Missing hosts raise (NXDOMAIN)."""

    async def _resolve(host: str):
        if host not in mapping:
            raise OSError(f"simulated NXDOMAIN for {host}")
        return mapping[host]

    return _resolve


# ---------------------------------------------------------------------------
# Literal IPs / baseline CIDR matching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_literal_ip_passes():
    await evaluate_url("http://93.184.216.34/", policy=None)


@pytest.mark.asyncio
async def test_loopback_literal_ip_blocked():
    with pytest.raises(EgressPolicyViolationError) as exc_info:
        await evaluate_url("http://127.0.0.1/", policy=None)
    assert exc_info.value.reason == "ip_in_denied_cidr"
    assert exc_info.value.matched_ip == "127.0.0.1"


@pytest.mark.asyncio
async def test_cloud_metadata_ip_blocked():
    with pytest.raises(EgressPolicyViolationError) as exc_info:
        await evaluate_url("http://169.254.169.254/latest/meta-data/", policy=None)
    assert exc_info.value.matched_ip == "169.254.169.254"


@pytest.mark.asyncio
async def test_rfc1918_literal_ip_blocked():
    for ip in ("10.0.0.5", "172.16.5.5", "192.168.1.1"):
        with pytest.raises(EgressPolicyViolationError):
            await evaluate_url(f"http://{ip}/", policy=None)


# ---------------------------------------------------------------------------
# DNS resolution: public hosts pass, malicious/rebound hosts fail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_hostname_resolves_and_passes():
    resolver = _resolver({"api.example.com": ["93.184.216.34"]})
    await evaluate_url("https://api.example.com/v1/data", policy=None, resolver=resolver)


@pytest.mark.asyncio
async def test_hostname_resolving_to_loopback_blocked():
    resolver = _resolver({"attacker.example.com": ["127.0.0.1"]})
    with pytest.raises(EgressPolicyViolationError) as exc_info:
        await evaluate_url("http://attacker.example.com/", policy=None, resolver=resolver)
    assert exc_info.value.matched_ip == "127.0.0.1"


@pytest.mark.asyncio
async def test_hostname_resolving_to_metadata_ip_blocked():
    resolver = _resolver({"attacker.example.com": ["169.254.169.254"]})
    with pytest.raises(EgressPolicyViolationError) as exc_info:
        await evaluate_url("http://attacker.example.com/", policy=None, resolver=resolver)
    assert exc_info.value.matched_ip == "169.254.169.254"


@pytest.mark.asyncio
async def test_dns_rebinding_second_record_blocked():
    """A hostname that answers with one public IP AND one metadata IP must still be blocked --
    the classic DNS-rebinding shape (TOCTOU against a naive first-record-only check)."""
    resolver = _resolver({"rebind.example.com": ["93.184.216.34", "169.254.169.254"]})
    with pytest.raises(EgressPolicyViolationError) as exc_info:
        await evaluate_url("http://rebind.example.com/", policy=None, resolver=resolver)
    assert exc_info.value.matched_ip == "169.254.169.254"


@pytest.mark.asyncio
async def test_dns_resolution_failure_fails_closed():
    resolver = _resolver({})
    with pytest.raises(EgressPolicyViolationError) as exc_info:
        await evaluate_url("http://nxdomain.example.com/", policy=None, resolver=resolver)
    assert "dns_resolution_failed" in exc_info.value.reason


@pytest.mark.asyncio
async def test_dns_timeout_fails_closed():
    async def _hang(host: str):
        import asyncio

        await asyncio.sleep(10)
        return ["93.184.216.34"]  # pragma: no cover

    with pytest.raises(EgressPolicyViolationError) as exc_info:
        await evaluate_url("http://slow.example.com/", policy=None, resolver=_hang, timeout=0.05)
    assert "dns_resolution_failed" in exc_info.value.reason


@pytest.mark.asyncio
async def test_empty_dns_answer_fails_closed():
    resolver = _resolver({"empty.example.com": []})
    with pytest.raises(EgressPolicyViolationError) as exc_info:
        await evaluate_url("http://empty.example.com/", policy=None, resolver=resolver)
    assert exc_info.value.reason == "dns_resolution_empty"


# ---------------------------------------------------------------------------
# Enterprise CIDR overrides
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_additional_denied_cidr_blocks_custom_enterprise_subnet():
    # 34.200.0.0/16 is globally routable (not in the RFC1918/loopback/metadata baseline),
    # so this only gets blocked if the enterprise override is actually applied.
    policy = {"additional_denied_cidrs": ["34.200.0.0/16"]}
    resolver = _resolver({"vendor.example.com": ["34.200.5.7"]})
    with pytest.raises(EgressPolicyViolationError) as exc_info:
        await evaluate_url("http://vendor.example.com/", policy=policy, resolver=resolver)
    assert exc_info.value.matched_rule == "34.200.0.0/16"


@pytest.mark.asyncio
async def test_additional_denied_cidr_does_not_affect_other_public_ips():
    policy = {"additional_denied_cidrs": ["34.200.0.0/16"]}
    resolver = _resolver({"api.example.com": ["93.184.216.34"]})
    await evaluate_url("http://api.example.com/", policy=policy, resolver=resolver)


# ---------------------------------------------------------------------------
# Domain wildcard filtering / ALLOWLIST_ONLY mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowlist_only_mode_blocks_domain_not_in_allowlist():
    policy = {"egress_mode": "ALLOWLIST_ONLY", "allowed_domains": ["api.github.com"]}
    resolver = _resolver({"internal.corp": ["93.184.216.34"], "api.github.com": ["140.82.112.6"]})
    with pytest.raises(EgressPolicyViolationError) as exc_info:
        await evaluate_url("http://internal.corp/", policy=policy, resolver=resolver)
    assert exc_info.value.reason == "allowlist_only_denied"


@pytest.mark.asyncio
async def test_allowlist_only_mode_permits_exact_allowed_domain():
    policy = {"egress_mode": "ALLOWLIST_ONLY", "allowed_domains": ["api.github.com"]}
    resolver = _resolver({"api.github.com": ["140.82.112.6"]})
    await evaluate_url("https://api.github.com/repos", policy=policy, resolver=resolver)


@pytest.mark.asyncio
async def test_allowlist_only_mode_wildcard_domain_blocks_non_matching_host():
    policy = {"egress_mode": "ALLOWLIST_ONLY", "allowed_domains": ["*.internal.corp"]}
    resolver = _resolver({"api.github.com": ["140.82.112.6"]})
    with pytest.raises(EgressPolicyViolationError):
        await evaluate_url("https://api.github.com/", policy=policy, resolver=resolver)


@pytest.mark.asyncio
async def test_allowlist_only_mode_wildcard_domain_permits_subdomain():
    policy = {"egress_mode": "ALLOWLIST_ONLY", "allowed_domains": ["*.internal.corp"]}
    resolver = _resolver({"tools.internal.corp": ["10.0.0.5"]})
    # Even an allow-listed host still has to clear the baseline CIDR denylist -- an
    # RFC1918 answer for an internal.corp subdomain is still rejected on IP grounds.
    with pytest.raises(EgressPolicyViolationError) as exc_info:
        await evaluate_url("https://tools.internal.corp/", policy=policy, resolver=resolver)
    assert exc_info.value.reason == "ip_in_denied_cidr"


@pytest.mark.asyncio
async def test_allowlist_only_mode_wildcard_domain_permits_subdomain_with_public_ip():
    policy = {"egress_mode": "ALLOWLIST_ONLY", "allowed_domains": ["*.internal.corp"]}
    resolver = _resolver({"tools.internal.corp": ["93.184.216.34"]})
    await evaluate_url("https://tools.internal.corp/", policy=policy, resolver=resolver)


@pytest.mark.asyncio
async def test_allowlist_only_mode_wildcard_does_not_match_bare_domain():
    policy = {"egress_mode": "ALLOWLIST_ONLY", "allowed_domains": ["*.internal.corp"]}
    resolver = _resolver({"internal.corp": ["93.184.216.34"]})
    with pytest.raises(EgressPolicyViolationError):
        await evaluate_url("https://internal.corp/", policy=policy, resolver=resolver)


def test_compile_policy_defaults_to_default_block_with_no_allowed_domains():
    compiled = compile_policy(None)
    assert compiled.mode == "DEFAULT_BLOCK"
    assert compiled.allowed_domains == ()


def test_compile_policy_rejects_unknown_mode_by_falling_back():
    compiled = compile_policy({"egress_mode": "NOT_A_REAL_MODE"})
    assert compiled.mode == "DEFAULT_BLOCK"


# ---------------------------------------------------------------------------
# Argument-tree URL discovery (what the MCP router actually calls)
# ---------------------------------------------------------------------------


def test_find_urls_walks_nested_arguments():
    arguments = {
        "target": "http://example.com/a",
        "nested": {"list": ["https://example.org/b", "not a url", 42]},
        "note": "fetch http://example.net/c and also http://example.com/a again",
    }
    urls = find_urls(arguments)
    assert urls == ["http://example.com/a", "https://example.org/b", "http://example.net/c"]


def test_find_urls_ignores_non_string_leaves():
    assert find_urls({"a": 1, "b": None, "c": [1, 2, {"d": True}]}) == []


@pytest.mark.asyncio
async def test_scan_arguments_raises_on_first_blocked_url_in_nested_payload():
    resolver = _resolver({"api.example.com": ["93.184.216.34"]})
    arguments = {
        "safe": "http://api.example.com/data",
        "nested": {"target": "http://169.254.169.254/latest/meta-data/iam/security-credentials/"},
    }
    with pytest.raises(EgressPolicyViolationError) as exc_info:
        await scan_arguments(arguments, policy=None, resolver=resolver)
    assert exc_info.value.matched_ip == "169.254.169.254"


@pytest.mark.asyncio
async def test_scan_arguments_passes_when_all_urls_are_public():
    resolver = _resolver({"api.example.com": ["93.184.216.34"], "cdn.example.org": ["104.16.132.229"]})
    arguments = {"a": "http://api.example.com/x", "b": {"c": "https://cdn.example.org/y"}}
    await scan_arguments(arguments, policy=None, resolver=resolver)


# ---------------------------------------------------------------------------
# Non-http(s) / malformed URLs are rejected rather than silently ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsupported_scheme_rejected():
    with pytest.raises(EgressPolicyViolationError) as exc_info:
        await evaluate_url("file:///etc/passwd", policy=None)
    assert exc_info.value.reason == "unsupported_scheme"


@pytest.mark.asyncio
async def test_missing_host_rejected():
    with pytest.raises(EgressPolicyViolationError) as exc_info:
        await evaluate_url("http:///path-only", policy=None)
    assert exc_info.value.reason == "missing_host"
