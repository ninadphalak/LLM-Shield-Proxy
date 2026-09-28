"""Test that a streaming filter gives the same output however its input is split into chunks.

Standard library only. The optional Hypothesis strategies live in ``chunk_invariance.strategies``
and need the ``hypothesis`` extra.
"""

from .adapters import from_async, per_chunk
from .core import (
    ChunkInvarianceError,
    all_splits,
    assert_chunk_invariant,
    assert_chunk_invariant_async,
    two_part_splits,
)

__version__ = "0.1.0"

__all__ = [
    "ChunkInvarianceError",
    "all_splits",
    "assert_chunk_invariant",
    "assert_chunk_invariant_async",
    "from_async",
    "per_chunk",
    "two_part_splits",
]
