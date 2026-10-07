"""Does Guardrails' accumulator drop text that is not whitespace?

**Why this exists.** The round-2 review flagged, without being able to run it, that
`_emit()` returns `""` when `validate_stream` yields a result whose `validated_chunk` is
unset -- which would silently drop text and look like perfect redaction. Executed, the
concern is real but narrower than that: the accumulator DOES lose characters, and every
one of them is inter-sentence whitespace that
`split_sentence_word_tokenizers_jl_separator` consumes when it splits.

**That is benign for the row and it is not benign in general.** The leak inspector matches
on `_normalize`d text, which strips non-alphanumerics, so losing a space cannot hide a
needle. Losing a *character* could: any dropped non-whitespace makes the client's text
shorter than what was sent, and a needle that spanned the drop would score as contained.
**The row would understate the leak rate and look like better redaction than it is.**

So the property to hold is not "nothing is lost" -- that is false and harmless -- it is
**"nothing but whitespace is lost"**. This script asserts that, over the shapes the corpus
actually produces plus the ones most likely to break a sentence tokenizer.

It cannot live in `tests/` because it needs the Guardrails environment, and software under
test does not go in the harness environment. Re-run it whenever the pinned Guardrails
version changes:

    venv-guardrails/Scripts/python benchmarks/guardrails-v2-profile/check_no_text_lost.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

GATEWAY = Path(__file__).with_name("gateway.py")

spec = importlib.util.spec_from_file_location("gw", GATEWAY)
gw = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(gw)


def drive(pieces: list[str]) -> str:
    """Feed the pieces through the accumulator exactly as the gateway does."""
    validator = gw.ProfilePii(on_fail="fix")
    out = ""
    for piece in pieces:
        out += gw._emit(validator.validate_stream(piece, {}))
    out += gw._emit(validator.validate_stream("", {}, remainder=True))
    return out


CASES: dict[str, list[str]] = {
    # The two shapes the corpus emits.
    "corpus: content carrier": ["Reference record: ", "somebody@e", "xample.com"],
    "corpus: prose then value": ["You sent: hello there. ", "Reference record: ", "x"],
    # Shapes chosen to break a sentence tokenizer.
    "no sentence terminator": ["abcdefghij", "klmnopqrst"],
    "value spans a full stop": ["Call 555.", "1234 now"],
    "newlines not spaces": ["line one\n", "line two\n", "line three"],
    "tabs": ["a\tb\tc ", "d\te\tf"],
    "unicode ellipsis": ["wait… ", "then more"],
    "many short chunks": list("a sentence split very finely. and another one."),
    "empty chunks interleaved": ["one. ", "", "two. ", "", "three"],
}


def main() -> int:
    failures: list[str] = []
    print(f"{'case':30} {'chars lost':>10}  verdict")
    for name, pieces in CASES.items():
        sent, got = "".join(pieces), drive(pieces)
        # Redaction legitimately shortens the text, so compare only when nothing matched.
        if "[REDACTED]" in got:
            sent = sent.replace("somebody@example.com", "[REDACTED]")
        lost = len(sent) - len(got)
        sent_nw, got_nw = "".join(sent.split()), "".join(got.split())
        ok = sent_nw == got_nw
        if not ok:
            failures.append(f"{name}: sent {sent_nw!r} got {got_nw!r}")
        print(f"{name:30} {lost:>10}  {'whitespace only' if ok else 'NON-WHITESPACE LOST'}")

    print()
    if failures:
        print("FAIL -- the accumulator dropped text that is not whitespace:")
        for f in failures:
            print("   ", f)
        print("\nThe published row understates its leak rate. Do not publish it.")
        return 1
    print("PASS -- only whitespace is lost, so no needle can be hidden by the accumulator.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
