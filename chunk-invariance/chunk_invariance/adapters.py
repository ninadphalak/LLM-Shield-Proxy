"""Turn common streaming-filter shapes into the ``stream_filter`` the assertion calls."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterable, AsyncIterator, Callable, Iterable, Iterator, Optional


def per_chunk(
    process: Callable[[Any, Any], Any],
    *,
    state: Callable[[], Any] = dict,
    flush: Optional[Callable[[Any], Any]] = None,
) -> Callable[[Iterable[Any]], Iterator[Any]]:
    """Adapt ``process(chunk, state) -> output`` plus an optional ``flush(state) -> output``.

    ``state`` is a factory called once per stream, so every split starts clean. ``flush`` is
    called after the last chunk; leave it out only if the filter really has no end-of-stream step.
    """

    def stream_filter(chunks: Iterable[Any]) -> Iterator[Any]:
        current = state()
        for chunk in chunks:
            yield process(chunk, current)
        if flush is not None:
            yield flush(current)

    return stream_filter


def from_async(
    async_stream_filter: Callable[[AsyncIterable[Any]], AsyncIterable[Any]],
) -> Callable[[Iterable[Any]], Iterator[Any]]:
    """Adapt an async generator function that takes an async iterable of chunks.

    Each stream runs to completion in its own event loop. Inside a running loop (an async test),
    use ``assert_chunk_invariant_async`` instead.
    """

    async def feed(chunks: Iterable[Any]) -> AsyncIterator[Any]:
        for chunk in chunks:
            yield chunk

    async def collect(chunks: Iterable[Any]) -> list:
        return [piece async for piece in async_stream_filter(feed(chunks))]

    def stream_filter(chunks: Iterable[Any]) -> Iterator[Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return iter(asyncio.run(collect(list(chunks))))
        raise RuntimeError("from_async cannot run inside an event loop; use assert_chunk_invariant_async")

    return stream_filter
