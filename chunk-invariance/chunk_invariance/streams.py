"""Splits shaped like the streams real model providers send.

A two-part split cuts the input once. Providers do not deliver text that way:

- Many send about one tokenizer token per event, so a value arrives in many small pieces,
  and a piece often ends right after a ``.``, ``?`` or ``!`` inside the value (``555.``,
  ``10.``, ``example.``). Others batch a few tokens per event.
- A stream has many chunk boundaries, not one. A filter can survive every single cut and
  still fail when three or more boundaries fall inside one value.

These generators produce such splits. They are deterministic, so a failure reproduces.
Standard library only. ``tiktoken`` is used for real byte-pair token boundaries when it is
installed (``pip install "chunk-invariance[tokens]"``); without it a small word and
punctuation tokenizer stands in, which splits digit runs and punctuation the same way.
"""

from __future__ import annotations

import random
import re
from typing import Any, Iterator, List, Optional, Sequence, Tuple

DEFAULT_MARKS = ".?!"

# Letters and symbol runs, each taking one leading space; digit runs of at most three with
# no leading space (byte-pair tokenizers group digits that way); then bare whitespace.
_SIMPLE_TOKEN = re.compile(r" ?[^\W\d_]+|\d{1,3}| ?[^\w\s]+| ?_+|\s+(?!\S)|\s")


def simple_tokens(text: str) -> List[str]:
    """A dependency-free stand-in for a byte-pair tokenizer: words, digit groups of up to
    three, punctuation runs, each with its leading space. Joins back to ``text``."""
    tokens = _SIMPLE_TOKEN.findall(text)
    if "".join(tokens) != text:  # pragma: no cover - the pattern covers every character
        raise AssertionError("tokenizer lost characters")
    return tokens


def tokenize(text: Any, encoding: Optional[str] = "auto") -> List[Any]:
    """Split ``text`` at token boundaries.

    ``encoding="auto"`` uses tiktoken's ``o200k_base`` when tiktoken is installed and the
    simple tokenizer otherwise. A named encoding (``"o200k_base"``, ``"cl100k_base"``)
    requires tiktoken. ``None`` or ``"simple"`` always uses the simple tokenizer. Bytes are
    split one byte per token, since token boundaries are defined on text.
    """
    if isinstance(text, bytes):
        return [text[i : i + 1] for i in range(len(text))]
    if encoding in (None, "simple"):
        return simple_tokens(text)
    try:
        import tiktoken  # type: ignore[import-not-found]
    except ImportError:
        if encoding == "auto":
            return simple_tokens(text)
        raise ImportError(f"encoding {encoding!r} needs tiktoken: pip install 'chunk-invariance[tokens]'") from None
    if encoding == "auto":
        try:
            enc = tiktoken.get_encoding("o200k_base")
        except Exception:  # noqa: BLE001
            # tiktoken fetches an encoding file on first use. Offline, with nothing cached,
            # that fails; the default check must not fail with it.
            return simple_tokens(text)
    else:
        enc = tiktoken.get_encoding(encoding)
    pieces = [enc.decode([t]) for t in enc.encode(text)]
    if "".join(pieces) != text:
        # A token that is part of a multi-byte character decodes to a replacement
        # character; fall back to the simple tokenizer rather than change the input.
        return simple_tokens(text)
    return pieces


def _cuts_of(chunks: Sequence[Any]) -> Tuple[int, ...]:
    cuts, offset = [], 0
    for chunk in chunks[:-1]:
        offset += len(chunk)
        cuts.append(offset)
    return tuple(cuts)


def _split_at(text: Any, cuts: Sequence[int]) -> List[Any]:
    bounds = [0, *cuts, len(text)]
    return [text[a:b] for a, b in zip(bounds, bounds[1:])]


def token_splits(
    text: Any, *, encoding: Optional[str] = "auto", group_sizes: Sequence[int] = (1, 2, 3, 4)
) -> Iterator[List[Any]]:
    """The input as a token stream: one token per chunk, then ``n`` tokens per chunk for
    each ``n`` in ``group_sizes`` (providers that batch)."""
    tokens = tokenize(text, encoding)
    for n in group_sizes:
        if n < 1:
            raise ValueError("group sizes must be at least 1")
        yield [
            "".join(tokens[i : i + n]) if isinstance(text, str) else b"".join(tokens[i : i + n])
            for i in range(0, len(tokens), n)
        ] or [text]


def _mark_positions(text: Any, marks: str) -> List[int]:
    """Offsets just after each mark that sits inside a value: followed by a non-space
    character, so it cannot be the end of a sentence."""
    out = []
    for i in range(len(text) - 1):
        char = text[i : i + 1]
        if isinstance(text, bytes):
            char = char.decode("latin-1")
        nxt = text[i + 1 : i + 2]
        if isinstance(text, bytes):
            nxt = nxt.decode("latin-1")
        if char in marks and not nxt.isspace():
            out.append(i + 1)
    return out


def punctuation_splits(
    text: Any, *, marks: str = DEFAULT_MARKS, encoding: Optional[str] = "auto"
) -> Iterator[List[Any]]:
    """Splits where a chunk ends right after a ``.``, ``?`` or ``!`` inside a value.

    For each such mark: the token stream with an extra cut right after it; everything up
    to the mark as one chunk followed by the token stream; the input cut once there; and
    three parts (up to the start of the word holding the mark, the word up to the mark,
    the rest).
    """
    tokens = tokenize(text, encoding)
    token_cuts = set(_cuts_of(tokens))
    for pos in _mark_positions(text, marks):
        yield _split_at(text, sorted(token_cuts | {pos}))
        yield _split_at(text, sorted({pos} | {c for c in token_cuts if c > pos}))
        yield _split_at(text, [pos])
        start = pos - 1
        while start > 0 and not text[start - 1 : start].isspace():
            start -= 1
        if 0 < start < pos:
            yield _split_at(text, [start, pos])


def fixed_size_splits(text: Any, sizes: Sequence[int] = (1, 2, 3, 4, 8)) -> Iterator[List[Any]]:
    """The input in chunks of ``n`` characters (or bytes) for each ``n``; 1 is one per chunk."""
    for n in sizes:
        if n < 1:
            raise ValueError("chunk sizes must be at least 1")
        yield [text[i : i + n] for i in range(0, len(text), n)] or [text]


def n_part_splits(
    text: Any, parts: Sequence[int] = (3, 4, 6, 8), *, samples: int = 8, seed: int = 0
) -> Iterator[List[Any]]:
    """Random splits into exactly ``n`` non-empty parts, ``samples`` per ``n``, seeded so
    the same input always gets the same splits."""
    rng = random.Random(f"{seed}:{len(text)}")
    positions = range(1, len(text))
    for n in parts:
        if n < 2:
            raise ValueError("an n-part split needs at least 2 parts")
        if n - 1 > len(positions):
            continue
        for _ in range(samples):
            yield _split_at(text, sorted(rng.sample(positions, n - 1)))


def stream_splits(text: Any, *, encoding: Optional[str] = "auto", marks: str = DEFAULT_MARKS) -> Iterator[List[Any]]:
    """Every stream shape above, duplicates removed: token streams, punctuation-terminated
    chunks, fixed sizes (including one character per chunk) and random many-part splits."""
    seen = set()
    sources = (
        token_splits(text, encoding=encoding),
        punctuation_splits(text, marks=marks, encoding=encoding),
        fixed_size_splits(text),
        n_part_splits(text),
    )
    for source in sources:
        for chunks in source:
            key = _cuts_of(chunks)
            if key in seen:
                continue
            seen.add(key)
            yield chunks
