"""A policy typo must not quietly turn an allowlist into "any public host".

`compile_policy` accepted only the exact strings DEFAULT_BLOCK and ALLOWLIST_ONLY and
silently replaced anything else with DEFAULT_BLOCK, the permissive one. Writing
`allowlist_only` in lower case therefore produced the opposite of what it says, with no
error and no warning, and every public host was allowed.
"""

from __future__ import annotations

import pytest

from llm_shield_proxy.security.egress_guard import compile_policy


@pytest.mark.parametrize("spelling", ["ALLOWLIST_ONLY", "allowlist_only", "Allowlist_Only", " allowlist_only "])
def test_case_and_spacing_do_not_change_the_meaning(spelling: str) -> None:
    compiled = compile_policy({"egress_mode": spelling, "allowed_domains": ["api.github.com"]})
    assert compiled.mode == "ALLOWLIST_ONLY", f"{spelling!r} silently became {compiled.mode}"


@pytest.mark.parametrize("spelling", ["DEFAULT_BLOCK", "default_block"])
def test_the_permissive_mode_still_works_when_it_is_asked_for(spelling: str) -> None:
    assert compile_policy({"egress_mode": spelling}).mode == "DEFAULT_BLOCK"


def test_no_policy_keeps_the_documented_default() -> None:
    """Absent config is not invalid config. The documented default is unchanged."""
    assert compile_policy(None).mode == "DEFAULT_BLOCK"
    assert compile_policy({}).mode == "DEFAULT_BLOCK"


@pytest.mark.parametrize("garbage", ["ALOWLIST_ONLY", "strict", "yes", "1"])
def test_an_unrecognised_mode_fails_closed(garbage: str) -> None:
    """Invariant 3: a gate denies on bad config rather than choosing the open option."""
    assert compile_policy({"egress_mode": garbage}).mode == "ALLOWLIST_ONLY"
