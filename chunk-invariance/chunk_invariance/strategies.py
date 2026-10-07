"""Hypothesis strategies for splits. Needs ``pip install chunk-invariance[hypothesis]``.

    from hypothesis import given
    from chunk_invariance import assert_chunk_invariant
    from chunk_invariance.strategies import splits_of

    @given(splits_of("use sk-AbCd1234 now"))
    def test_filter(chunks):
        assert_chunk_invariant(my_filter, redact, "".join(chunks), splits=[chunks])
"""

from __future__ import annotations

from typing import Any, List, Optional

try:
    from hypothesis import strategies as st
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError("chunk_invariance.strategies needs Hypothesis: pip install 'chunk-invariance[hypothesis]'") from exc


def splits_of(text: Any, max_parts: Optional[int] = None) -> "st.SearchStrategy[List[Any]]":
    """Random splits of one fixed input into non-empty parts; shrinks toward fewer cuts."""
    positions = list(range(1, len(text)))
    most = len(positions) if max_parts is None else max(max_parts - 1, 0)

    def build(cuts: List[int]) -> List[Any]:
        bounds = [0, *sorted(cuts), len(text)]
        return [text[a:b] for a, b in zip(bounds, bounds[1:])]

    if not positions or most == 0:
        return st.just([text])
    return st.lists(st.sampled_from(positions), unique=True, max_size=most).map(build)


@st.composite
def text_and_splits(draw: Any, texts: "st.SearchStrategy[Any]", max_parts: Optional[int] = None) -> Any:
    """Draw an input from ``texts``, then a split of it. Returns ``(text, chunks)``."""
    text = draw(texts)
    return text, draw(splits_of(text, max_parts))
