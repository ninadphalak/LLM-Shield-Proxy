"""The Vault allocation guard must be tested on its own, not through streaming.

`rehydrate_sse_stream` enforces the output ceiling twice: `Vault.rehydrate`
refuses to BUILD an oversized result, and `_bounded_output` refuses to EMIT one.
Only the second is exercised by the streaming tests, so deleting the first left
`test_repeated_token_amplification_fails_closed` passing -- verified by mutation.
That is the wrong signal: without the allocation guard the oversized string is
still materialised and only discarded afterwards, which is precisely the
per-request memory growth CONTEXT.md 1.5 forbids. These tests fail if the
allocation guard is removed.
"""

import pytest

from llm_shield_proxy.engines.vault import Vault

TOKEN = "[PERSON_1]"
ORIGINAL = "A" * 700


def _mapped_vault() -> Vault:
    vault = Vault(synthetic=False)
    vault.token_to_original[TOKEN] = ORIGINAL
    vault.original_to_token[ORIGINAL] = TOKEN
    vault.max_token_length = len(TOKEN)
    return vault


def test_repeated_token_cannot_allocate_past_the_ceiling():
    """Three 10-char tokens expanding to 700 chars each must fail before building."""
    vault = _mapped_vault()
    text = " ".join([TOKEN] * 3)
    # 3 * 700 = 2100 characters of original data from a 32-character input.
    with pytest.raises(ValueError, match="maximum safe length"):
        vault.rehydrate(text, 0, 1536)


def test_oversized_input_is_refused_before_any_replacement():
    """The guard rejects input already past the ceiling without scanning it."""
    vault = _mapped_vault()
    with pytest.raises(ValueError, match="maximum safe length"):
        vault.rehydrate("B" * 2000, 0, 1536)


def test_guard_is_opt_in_so_the_uncapped_api_is_unchanged():
    """Omitting the ceiling must preserve the historical unbounded behaviour."""
    vault = _mapped_vault()
    text = " ".join([TOKEN] * 3)
    assert vault.rehydrate(text, 0) == " ".join([ORIGINAL] * 3)


def test_expansion_within_the_ceiling_still_rehydrates():
    """The guard must not fire on legitimate expansion below the limit."""
    vault = _mapped_vault()
    assert vault.rehydrate(TOKEN, 0, 1536) == ORIGINAL
