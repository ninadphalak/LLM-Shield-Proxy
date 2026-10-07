"""A reproducible LOCAL word estimate for the IEEE Software submission.

The manuscript's budget has been carried in review prose as a bare number ("3,308 text
+ 52 headings"), with no way for a reviewer to reproduce it or to see which convention
produced it. Two plausible conventions differ by roughly the entire remaining margin, so
an unreproducible count is not a check -- it is an assertion.

This is NOT the IEEE analyzer and does not claim to agree with it. It states one
convention explicitly, applies it the same way every time, and prints the parts
separately so a disagreement can be localised rather than argued about.

Convention:
  * Count the document body only: between \\begin{document} and \\end{document}.
  * Float environments (figure, figure*, table, table*) are removed and counted
    separately at a fixed per-float allowance, because their captions and cells are not
    body text. IEEE charges a float an allowance rather than its literal words.
  * The bibliography is excluded; IEEE counts references separately.
  * Section headings are counted separately from body text.
  * A "word" is a whitespace-delimited token containing at least one alphanumeric
    character, after LaTeX control sequences and math/grouping punctuation are stripped.

Usage:
    python benchmarks/manuscript_wordcount.py <file.tex> [--limit 4200] [--float-cost 250]
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

FLOAT_ENVIRONMENTS = ("figure*", "figure", "table*", "table")


def _strip_environment(text: str, name: str) -> tuple[str, int]:
    """Remove every instance of one environment, returning the text and a count."""
    pattern = re.compile(
        r"\\begin\{" + re.escape(name) + r"\}.*?\\end\{" + re.escape(name) + r"\}",
        re.DOTALL,
    )
    removed = len(pattern.findall(text))
    return pattern.sub("", text), removed


def _words(text: str) -> list[str]:
    text = re.sub(r"(?<!\\)%.*", "", text)
    text = re.sub(r"\\[a-zA-Z@]+\*?", " ", text)
    text = re.sub(r"[{}$&~^_\\]", " ", text)
    return [token for token in text.split() if re.search(r"[A-Za-z0-9]", token)]


def count(source: str, float_cost: int) -> dict[str, object]:
    if "\\begin{document}" in source:
        body = source.split("\\begin{document}", 1)[1].split("\\end{document}", 1)[0]
    else:
        body = source

    floats = 0
    for environment in FLOAT_ENVIRONMENTS:
        body, removed = _strip_environment(body, environment)
        floats += removed

    body, _ = _strip_environment(body, "thebibliography")

    headings = re.findall(r"\\(?:sub)*section\*?\{([^}]*)\}", body)
    heading_words = sum(len(_words(heading)) for heading in headings)
    body = re.sub(r"\\(?:sub)*section\*?\{[^}]*\}", " ", body)

    text_words = len(_words(body))
    return {
        "text_words": text_words,
        "headings": len(headings),
        "heading_words": heading_words,
        "floats": floats,
        "float_allowance": floats * float_cost,
        "total": text_words + heading_words + floats * float_cost,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="path to the .tex manuscript")
    parser.add_argument("--limit", type=int, default=4200)
    parser.add_argument("--float-cost", type=int, default=250)
    args = parser.parse_args(argv)

    path = pathlib.Path(args.source)
    if not path.exists():
        print(f"{path}: not found", file=sys.stderr)
        return 2

    result = count(path.read_text(encoding="utf-8", errors="replace"), args.float_cost)
    total = int(result["total"])

    print(f"source           {path}")
    print(f"body text        {result['text_words']}")
    print(f"headings         {result['heading_words']} words in {result['headings']}")
    print(f"floats           {result['floats']} x {args.float_cost} = {result['float_allowance']}")
    print(f"total            {total} / {args.limit}")
    print(f"remaining        {args.limit - total}")
    print()
    print("Local estimate under the convention in this file's docstring.")
    print("The official IEEE analyzer and word counter remain authoritative.")
    return 0 if total <= args.limit else 1


if __name__ == "__main__":
    raise SystemExit(main())
