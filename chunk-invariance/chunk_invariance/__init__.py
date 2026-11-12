"""Test that a streaming filter gives the same output however its input is split into chunks.

Standard library only. The optional Hypothesis strategies live in ``chunk_invariance.strategies``
and need the ``hypothesis`` extra; ``tokens`` adds tiktoken for real token boundaries.
"""

from .adapters import from_async, per_chunk
from .core import (
    ChunkInvarianceError,
    all_splits,
    assert_chunk_invariant,
    assert_chunk_invariant_async,
    two_part_splits,
)
from .streams import (
    fixed_size_splits,
    n_part_splits,
    punctuation_splits,
    simple_tokens,
    stream_splits,
    token_splits,
    tokenize,
)

__version__ = "0.3.0"

__all__ = [
    "ChunkInvarianceError",
    "all_splits",
    "assert_chunk_invariant",
    "assert_chunk_invariant_async",
    "fixed_size_splits",
    "from_async",
    "n_part_splits",
    "per_chunk",
    "punctuation_splits",
    "simple_tokens",
    "stream_splits",
    "token_splits",
    "tokenize",
    "two_part_splits",
]
