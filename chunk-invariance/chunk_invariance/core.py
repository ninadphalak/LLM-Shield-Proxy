"""The invariant, the splits it is checked over, and the assertion."""

from __future__ import annotations

import inspect
from itertools import combinations
from typing import Any, AsyncIterable, AsyncIterator, Callable, Iterable, Iterator, List, Optional, Sequence, Union

Text = Union[str, bytes]
StreamFilter = Callable[[Iterable[Any]], Iterable[Any]]
WholeFilter = Callable[[Any], Any]
Splits = Union[str, int, Iterable[Sequence[Any]]]

# `splits="all"` enumerates 2 ** (len(text) - 1) splits; past this length that is too many
# to run in a test. Pass an int (the most parts per split) for longer inputs.
ALL_SPLITS_MAX_LENGTH = 16


class ChunkInvarianceError(AssertionError):
    """The streamed output for one split differs from filtering the whole input at once."""

    def __init__(self, text: Text, chunks: Sequence[Text], expected: Text, actual: Text, splits_tried: int) -> None:
        self.text = text
        self.chunks = list(chunks)
        self.expected = expected
        self.actual = actual
        self.splits_tried = splits_tried
        cuts = _cut_points(self.chunks)
        super().__init__(
            "stream filter is not chunk-invariant\n"
            f"  input:    {text!r}\n"
            f"  split:    {self.chunks!r} (cut at {', '.join(map(str, cuts)) or 'nothing: one chunk'})\n"
            f"  whole:    {expected!r}\n"
            f"  streamed: {actual!r}\n"
            f"  split #{splits_tried} in the order tried; generated splits run fewest parts first"
        )


def _cut_points(chunks: Sequence[Text]) -> List[int]:
    cuts, offset = [], 0
    for chunk in chunks[:-1]:
        offset += len(chunk)
        cuts.append(offset)
    return cuts


def _split_at(text: Text, cuts: Sequence[int]) -> List[Text]:
    bounds = [0, *cuts, len(text)]
    return [text[a:b] for a, b in zip(bounds, bounds[1:])]


def two_part_splits(text: Text) -> Iterator[List[Text]]:
    """Every split of ``text`` into two non-empty parts, left cut first."""
    for cut in range(1, len(text)):
        yield [text[:cut], text[cut:]]


def all_splits(text: Text, max_parts: Optional[int] = None) -> Iterator[List[Text]]:
    """Every split of ``text`` into at most ``max_parts`` non-empty parts (all of them when None).

    Ordered by number of parts, then by cut positions, so the first failure found is a minimal one.
    The single-part split ``[text]`` comes first.
    """
    if max_parts is not None and max_parts < 1:
        raise ValueError("max_parts must be at least 1")
    positions = range(1, len(text))
    most = len(text) if max_parts is None else min(max_parts, max(len(text), 1))
    for parts in range(1, most + 1):
        for cuts in combinations(positions, parts - 1):
            yield _split_at(text, cuts)


def _resolve_splits(text: Text, splits: Splits) -> Iterable[Sequence[Text]]:
    if splits == "all-two-part":
        return all_splits(text, 2)
    if splits == "all":
        if len(text) > ALL_SPLITS_MAX_LENGTH:
            raise ValueError(
                f'splits="all" is 2 ** {len(text) - 1} splits for a {len(text)}-character input; '
                f"use it up to {ALL_SPLITS_MAX_LENGTH} characters, or pass an int (the most parts per split)"
            )
        return all_splits(text)
    if isinstance(splits, bool):
        raise TypeError("splits must be 'all-two-part', 'all', an int, or an iterable of chunk lists")
    if isinstance(splits, int):
        return all_splits(text, splits)
    if isinstance(splits, (str, bytes)):
        raise ValueError(f"unknown splits value {splits!r}; use 'all-two-part', 'all', an int, or chunk lists")
    return splits


def _join(text: Text, pieces: Iterable[Text]) -> Text:
    return text[:0].join(pieces)


def assert_chunk_invariant(
    stream_filter: StreamFilter,
    whole_filter: WholeFilter,
    text: Text,
    *,
    splits: Splits = "all-two-part",
) -> int:
    """Assert that streaming ``text`` in any of ``splits`` gives what filtering it whole gives.

    ``stream_filter`` takes an iterable of input chunks and returns or yields output chunks; it is
    called once per split, so it must start from fresh state each call. ``whole_filter`` takes the
    whole input. ``splits`` is ``"all-two-part"`` (the default: the input whole, then every single
    cut), ``"all"`` (every split, for short inputs), an int (every split into at most that many
    parts), or an iterable of explicit chunk lists, which must each join back to ``text``.

    Raises ``ChunkInvarianceError`` on the first split, fewest parts first, whose concatenated
    output differs. Returns the number of splits checked.
    """
    expected = whole_filter(text)
    tried = 0
    for chunks in _resolve_splits(text, splits):
        chunks = list(chunks)
        if _join(text, chunks) != text:
            raise ValueError(f"explicit split {chunks!r} does not join back to the input")
        tried += 1
        actual = _join(text, stream_filter(iter(chunks)))
        if actual != expected:
            raise ChunkInvarianceError(text, chunks, expected, actual, tried)
    return tried


async def assert_chunk_invariant_async(
    stream_filter: Callable[[AsyncIterable[Any]], AsyncIterable[Any]],
    whole_filter: Callable[[Any], Any],
    text: Text,
    *,
    splits: Splits = "all-two-part",
) -> int:
    """``assert_chunk_invariant`` for an async generator filter, for use inside an async test.

    ``stream_filter`` takes an async iterable of chunks and yields output chunks. ``whole_filter``
    may return the output or an awaitable of it.
    """

    async def feed(chunks: Sequence[Text]) -> AsyncIterator[Text]:
        for chunk in chunks:
            yield chunk

    expected = whole_filter(text)
    if inspect.isawaitable(expected):
        expected = await expected
    tried = 0
    for chunks in _resolve_splits(text, splits):
        chunks = list(chunks)
        if _join(text, chunks) != text:
            raise ValueError(f"explicit split {chunks!r} does not join back to the input")
        tried += 1
        actual = _join(text, [piece async for piece in stream_filter(feed(chunks))])
        if actual != expected:
            raise ChunkInvarianceError(text, chunks, expected, actual, tried)
    return tried
