"""Three streaming redactors for one pattern: one correct, two that leak across a chunk boundary.

The pattern is a fixed-length key, ``sk-`` plus eight letters or digits, replaced by ``[KEY]``.
``redact`` is the whole-input filter all three are compared against.

- ``hold_back`` is correct. It holds back the last ``KEY_LENGTH - 1`` characters, the longest
  tail that could still be the start of a key, and redacts before it emits.
- ``design_a`` redacts each chunk on its own. A key cut in two is never seen whole.
- ``design_b`` prepends a fixed carryover from the previous chunk and redacts the join. The match
  fires, but the start of the key already left with the previous chunk.

Run ``assert_chunk_invariant(design_a, redact, "use sk-AbCd1234 now")`` to see the failure.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, Iterator

from .adapters import per_chunk

KEY = re.compile(r"sk-[A-Za-z0-9]{8}")
KEY_LENGTH = 11
MASK = "[KEY]"


def redact(text: str) -> str:
    """The whole-input filter: every key replaced."""
    return KEY.sub(MASK, text)


def _hold_back_process(chunk: str, state: Dict[str, str]) -> str:
    buffer = state.get("buffer", "") + chunk
    # A key that starts before `safe` ends inside the buffer, so it is already complete.
    safe = len(buffer) - (KEY_LENGTH - 1)
    out, emitted = [], 0
    for match in KEY.finditer(buffer):
        if match.start() >= safe:
            break
        out.append(buffer[emitted : match.start()])
        out.append(MASK)
        emitted = match.end()
    keep_from = max(emitted, safe)
    out.append(buffer[emitted:keep_from])
    state["buffer"] = buffer[keep_from:]
    return "".join(out)


def _hold_back_flush(state: Dict[str, str]) -> str:
    rest, state["buffer"] = state.get("buffer", ""), ""
    return redact(rest)


hold_back = per_chunk(_hold_back_process, state=dict, flush=_hold_back_flush)
hold_back.__doc__ = "Correct: holds back a possible key prefix and redacts before emitting."


def design_a(chunks: Iterable[str]) -> Iterator[str]:
    """Leaks: each chunk is redacted alone, so a key split across two chunks passes."""
    for chunk in chunks:
        yield redact(chunk)


def design_b(chunks: Iterable[str], carry: int = KEY_LENGTH - 1) -> Iterator[str]:
    """Leaks: prepends the last ``carry`` characters of the previous chunk and redacts the join.

    The match now fires, but the carried characters were already emitted with the previous
    chunk, so the start of a split key has gone out unredacted, and cutting the carry off the
    redacted join corrupts what follows.
    """
    previous = ""
    for chunk in chunks:
        joined = previous + chunk
        yield redact(joined)[len(previous) :]
        previous = joined[-carry:] if carry else ""
