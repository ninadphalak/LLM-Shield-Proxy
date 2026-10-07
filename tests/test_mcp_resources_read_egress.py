"""Every supported MCP method goes through the egress gate, not just `tools/call`.

`SUPPORTED_METHODS` contains `resources/read`, whose entire purpose is to fetch a URI.
Both the RBAC check and the SSRF gate used to live inside `if method == "tools/call":`,
so a `resources/read` naming the cloud metadata endpoint was forwarded upstream with no
egress check at all.

The gate also scans the whole of `params` rather than only `params["arguments"]`. A URL
in a sibling field such as `_meta` reaches an upstream tool exactly as readily as one
inside `arguments`.
"""

from __future__ import annotations

import pytest

from llm_shield_proxy.security.egress_guard import (
    EgressPolicyViolationError,
    scan_arguments,
)

# Targets an SSRF attempt would name. All are denied by the default internal ranges.
BLOCKED_URLS = [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:6379/",
    "http://10.0.0.1/admin",
    "http://[::1]/",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", BLOCKED_URLS)
async def test_scan_covers_a_resources_read_uri(url: str) -> None:
    """`resources/read` carries its target in `params["uri"]`, not in `arguments`."""
    params = {"uri": url}
    with pytest.raises(EgressPolicyViolationError):
        await scan_arguments(params, {"mode": "DENY_INTERNAL"})


@pytest.mark.asyncio
async def test_scan_covers_sibling_params_not_just_arguments() -> None:
    """A URL beside `arguments` is forwarded just as readily as one inside it."""
    params = {
        "name": "fetch",
        "arguments": {"q": "harmless"},
        "_meta": {"callbackUrl": "http://169.254.169.254/latest/meta-data/"},
    }
    with pytest.raises(EgressPolicyViolationError):
        await scan_arguments(params, {"mode": "DENY_INTERNAL"})

    # The old call site passed only this, which is why the sibling got through.
    await scan_arguments(params.get("arguments", {}), {"mode": "DENY_INTERNAL"})


@pytest.mark.asyncio
async def test_an_ordinary_public_url_is_still_allowed() -> None:
    """The gate must not block normal traffic."""

    async def resolver(host: str) -> list[str]:
        return ["93.184.216.34"]

    await scan_arguments(
        {"uri": "https://api.example.com/v1/thing"},
        {"mode": "DENY_INTERNAL"},
        resolver=resolver,
    )


@pytest.mark.asyncio
async def test_the_router_gates_every_supported_method() -> None:
    """Pins the structural fix: the gate is not inside a per-method branch.

    Reads the router source rather than driving HTTP, because the failure was a control
    flow one: the call existed, it was just unreachable for two of the three methods.
    """
    import ast
    import inspect
    import textwrap

    from llm_shield_proxy.api import mcp_router as module

    source = textwrap.dedent(inspect.getsource(module._process_single_call))
    tree = ast.parse(source)

    def _gate_calls_under(node) -> list:
        """Every `scan_arguments(params, ...)` call anywhere beneath `node`."""
        found = []
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            fn = child.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if name != "scan_arguments":
                continue
            first = child.args[0] if child.args else None
            if isinstance(first, ast.Name) and first.id == "params":
                found.append(child)
        return found

    assert _gate_calls_under(tree), "the egress gate call is gone entirely"

    # The real invariant is NOT textual ordering, which was the original assertion here.
    # It is that the gate is not NESTED inside a per-method branch, because that is what
    # made it unreachable for `resources/read` and `tools/list`. Ordering was only ever a
    # proxy for that, and it broke the moment a tool-authorization branch was correctly
    # hoisted above the gate: authorization has to precede attacker-controlled DNS work.
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if "tools/call" not in ast.dump(node.test):
            continue
        assert not _gate_calls_under(node), (
            "the egress gate is nested inside a tools/call branch, so methods other than "
            "tools/call skip it entirely"
        )
