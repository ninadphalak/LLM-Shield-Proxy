"""An upstream-chosen property name was compiled into a regex verbatim.

`StatelessStreamingLexer.feed_chunk` built its lookup pattern by interpolation::

    prop_pattern = re.compile(rf'"{prop}"\\s*:\\s*"([^"]*)"')

`prop` is not ours. It comes out of the upstream response, captured by
`"_ctx_hash_([^"]+)"`, and `[^"]+` admits every regex metacharacter there is. The
lexer runs per chunk on the SSE hot path (`api/main.py` builds one per streamed
response), so both failure modes below are reachable by anything that can influence
what the model or a compromised upstream emits.

Measured before the fix:

- ``_ctx_hash_foo(`` raised ``PatternError: missing ), unterminated subpattern``
  straight out of `feed_chunk`.
- ``_ctx_hash_(a+)+b`` backtracked exponentially. Padding 14 chars took 0.001s, 22
  took 0.143s, roughly x4 per two characters, and 40 characters did not finish inside
  two minutes.

One `re.escape` closes both. The tests keep the padding small on purpose: the point is
the scaling, and a test that actually triggered the unfixed blowup would hang CI rather
than fail it.
"""

from __future__ import annotations

import time

import pytest

from llm_shield_proxy.engines.stateless_mutation_engine.streaming_lexer import (
    StatelessStreamingLexer,
)


class _FakeCipher:
    """Stands in for StatelessPIICipher; the lexer only ever calls decrypt."""

    def decrypt(self, token: str, prop: str) -> str:
        return "PLAINTEXT"


def _feed(prop: str, value: str = "masked") -> str:
    """Everything the lexer emits for one chunk.

    `feed_chunk` keeps a 256-character trailing window and returns "" below that, so a
    short chunk surfaces only through `flush`. Asserting on `feed_chunk` alone would be
    asserting on an empty string and would pass for the wrong reason.
    """
    lexer = StatelessStreamingLexer(_FakeCipher())
    emitted = lexer.feed_chunk(
        '{"_ctx_hash_' + prop + '": "tok", "' + prop + '": "' + value + '"}'
    )
    return emitted + lexer.flush()


@pytest.mark.parametrize(
    "prop",
    [
        "foo(",           # unterminated subpattern
        "foo[",           # unterminated character set
        "foo)",           # unbalanced close
        "a{2,1}",         # invalid repeat bounds
        "*bad",           # nothing to repeat
        "foo\\",          # trailing backslash
    ],
)
def test_a_metacharacter_in_a_property_name_does_not_raise(prop: str):
    """A property name is data. It must never be compiled as a pattern."""
    _feed(prop)


def test_a_nested_quantifier_does_not_backtrack():
    """The ReDoS. Kept small deliberately; unfixed, this class of input hangs.

    24 characters is already ~0.5s unfixed and grows about fourfold every two
    characters, so a generous ceiling here still fails loudly on a regression
    without risking a CI hang.
    """
    started = time.perf_counter()
    _feed("(a+)+b", value="a" * 24)
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0, f"took {elapsed:.3f}s; the property name is being compiled again"


def test_a_metacharacter_property_still_rehydrates():
    """Escaping must not break the feature: the value is still replaced.

    `.` is the case that would silently keep working as a wildcard and quietly match
    the wrong field, so it is the one worth pinning.
    """
    out = _feed("a.c")

    assert "PLAINTEXT" in out
    assert "masked" not in out


def test_an_ordinary_property_still_rehydrates():
    """Control: the unremarkable path is unchanged."""
    out = _feed("email")

    assert "PLAINTEXT" in out
    assert "masked" not in out


def test_a_dot_does_not_match_a_different_property():
    """`a.c` must not rehydrate `abc`. Unescaped, the wildcard matched it."""
    lexer = StatelessStreamingLexer(_FakeCipher())
    out = lexer.feed_chunk('{"_ctx_hash_a.c": "tok", "abc": "masked"}') + lexer.flush()

    assert "masked" in out, "a wildcard matched a property the upstream did not name"
