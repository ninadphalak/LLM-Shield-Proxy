"""Keep public documentation links pointing at files that exist.

Deliberately a separate module from `test_public_docs_style.py`. The conformance
intake runs that file by name before it commits a results-wall row
(`scripts/process_conformance_submission.py`), and a stranger's one-line JSON row
has nothing to do with whether a docs link resolves. Putting these checks there
would make every submission fail on an unrelated broken link. `ci.yml` runs the
whole `tests/` tree, so this module gates pull requests without touching intake.

The failure this exists to catch: a relative link written without its file
extension resolves on the built site, because Docusaurus rewrites it to a route,
and 404s when the same page is read as a file on GitHub. Both READMEs send
readers to `website/docs/conformance/ci.mdx` on GitHub, so that second rendering
is a real surface, and a green `npm run build` says nothing about it. Three such
links shipped on the CI page alone, and a sweep that stopped at the first '#'
then missed three more that carried an anchor fragment.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "website" / "docs"
DOC_SUFFIXES = (".md", ".mdx")

# Public files that link into the repository tree and are read on GitHub.
PUBLIC_ROOT_FILES = ("README.md", "CONTRIBUTING.md", "STABILITY.md", "LIMITATIONS.md")
PUBLIC_NESTED_FILES = ("pii-leak-benchmark/README.md",)

# The fragment is captured separately so an anchored link is still checked. Matching
# it with the path is what let `./page#section` slip through a previous sweep.
#
# The `./` prefix is optional on purpose: `](supported-pii-types.md)` is the same kind
# of link as `](./supported-pii-types.md)` and four of them are already in the tree, so
# a pattern that demanded the prefix would have exempted them silently. A leading `/`
# is excluded because that is a site route, not a file path, and rewriting the repo's
# absolute `/docs/...` convention is a separate decision from checking links resolve.
#
# The destination may be wrapped in angle brackets and may carry a title, so
# `](<./page>)` and `](./page "Title")` match too. Written either way it is the
# same link, and a pattern that saw only the bare form would pass it unread.
_DESTINATION = r"""
    \]\(\s*<?                      # opening bracket, optional angle bracket
    (?!https?://|\#|mailto:|/)     # not absolute, an anchor, or a site route
    ([^)>\#?\s]+)                  # the path itself
    (?:[\#?][^)>\s]*)?             # anchor or query, kept out of the path
    >?(?:\s+"[^"]*")?\s*\)         # optional angle close, title, whitespace
"""
RELATIVE_LINK = re.compile(_DESTINATION, re.VERBOSE)
REPO_RELATIVE_LINK = RELATIVE_LINK
# A reference-style link keeps its destination in a definition line instead.
#
# Two shapes have to be kept out. `[^1]: some prose` is a GFM footnote, excluded
# by the label guard. `[Note]: this is prose not a link` is not a definition at
# all, and is excluded by requiring what CommonMark requires: the destination is
# followed by the end of the line or by a title, and nothing else. An earlier
# attempt demanded the destination "look like a path" instead, which did stop the
# prose but also hid `[pii]: supported-pii-types`, a bare name that is exactly the
# broken link this module exists to catch.
REFERENCE_DEFINITION = re.compile(
    r"""^\[(?!\^)[^\]]+\]:[ \t]*          # label, not a footnote
        <?(?!https?://|\#|mailto:|/)      # not absolute, an anchor, or a route
        ([^>\s\#?]+)                      # the destination
        (?:[\#?][^>\s]*)?>?[ \t]*         # anchor or query, optional bracket
        (?:"[^"]*"|'[^']*'|\([^)]*\))?    # an optional title, and nothing else
        [ \t]*$""",
    re.MULTILINE | re.VERBOSE,
)
# Restricted to refs that describe the current tree. A link pinned to a tag names a
# path as it was then, so checking that against today's files would be wrong.
GITHUB_BLOB_LINK = re.compile(
    r"https://(?:github\.com/ninadphalak/LLM-Shield-Proxy/(?:blob|tree)"
    r"|raw\.githubusercontent\.com/ninadphalak/LLM-Shield-Proxy)"
    r"/(?:main|HEAD)/([^)\s#?]+)"
)


def _doc_files() -> list[Path]:
    """Fail closed. A gate that finds no inputs must not report success.

    If `website/docs` is renamed or this module is run from outside the tree,
    `rglob` yields nothing and every assertion below passes having checked
    nothing. That is the exact shape of the bug this module exists to catch, so
    it is an error here rather than a quiet pass.
    """
    files = sorted(
        path
        for suffix in DOC_SUFFIXES
        for path in DOCS_ROOT.rglob(f"*{suffix}")
        if path.is_file()
    )
    assert files, f"no documentation found under {DOCS_ROOT}; this gate would pass vacuously"
    return files


def test_relative_doc_links_carry_their_file_extension() -> None:
    """An extensionless relative link resolves on the site and 404s on GitHub."""
    failures: list[str] = []
    for path in _doc_files():
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            # A reference definition names a destination just as an inline link
            # does, so a bare page name written that way is the same bug.
            matches = [m for p in (RELATIVE_LINK, REFERENCE_DEFINITION) for m in p.finditer(line)]
            for match in matches:
                target = match.group(1)
                if target.endswith(DOC_SUFFIXES):
                    continue
                # A trailing slash says "directory" outright, so there is no
                # missing extension to report and no sensible suffix to suggest.
                # Whether that directory exists is the next test's business, and
                # it no longer exempts one.
                if target.endswith("/"):
                    continue
                # A non-document asset keeps whatever extension it has; only a
                # bare page reference is the bug.
                if Path(target).suffix:
                    continue
                # An extensionless target that is a real file or a directory
                # resolves everywhere. LICENSE, Makefile and a section directory
                # are all legitimate; only a bare PAGE name is the bug.
                resolved = (path.parent / target).resolve()
                if resolved.is_file() or resolved.is_dir():
                    continue
                # Name the fix rather than hint at it: the suffix is whichever one
                # the target happens to have, and guessing .md is how an .mdx page
                # gets a link that still does not resolve.
                suffix = next(
                    (s for s in DOC_SUFFIXES if resolved.with_suffix(s).exists()), None
                )
                remedy = f"write '{target}{suffix}'" if suffix else "no such page exists"
                relative_path = path.relative_to(REPO_ROOT)
                failures.append(
                    f"{relative_path}:{line_number}: '{target}' has no file extension, so it "
                    f"resolves on the built site and 404s on GitHub. Fix: {remedy}."
                )

    assert not failures, "\n" + "\n".join(failures)


def test_relative_doc_links_point_at_a_file_that_exists() -> None:
    """The extension has to be the one the target actually has, not a guess."""
    failures: list[str] = []
    for path in _doc_files():
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            # Inline destinations and reference-style definitions both name a
            # file, so both are checked. A broken target hidden in a `[ref]:`
            # line is no less broken for being written out of line.
            for pattern in (RELATIVE_LINK, REFERENCE_DEFINITION):
                for match in pattern.finditer(line):
                    target = match.group(1)
                    # No skip for a trailing slash. `](./who-has-run-it/)` names a
                    # directory that does not exist and 404s on GitHub exactly like
                    # a missing file; exempting it made the gate pass a broken link.
                    if not (path.parent / target).resolve().exists():
                        relative_path = path.relative_to(REPO_ROOT)
                        failures.append(
                            f"{relative_path}:{line_number}: '{target}' does not exist"
                        )

    assert not failures, "\n" + "\n".join(failures)


def test_public_readme_links_into_the_repository_resolve() -> None:
    """Both READMEs pointed at `conformance/ci.md` after the page became `ci.mdx`."""
    failures: list[str] = []
    for relative_name in (*PUBLIC_ROOT_FILES, *PUBLIC_NESTED_FILES):
        path = REPO_ROOT / relative_name
        if not path.exists():
            # Fail closed, as above. Skipping here would mean a rename or a typo
            # in the list silently stops checking that file.
            failures.append(f"{relative_name}: listed as public but not in the tree")
            continue
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            # A path relative to the file itself, as GitHub renders it, written
            # either inline or as a reference definition.
            for pattern in (REPO_RELATIVE_LINK, REFERENCE_DEFINITION):
                for match in pattern.finditer(line):
                    target = match.group(1)
                    if not (path.parent / target).resolve().exists():
                        failures.append(
                            f"{relative_name}:{line_number}: '{target}' does not exist"
                        )
            # An absolute blob/tree URL naming a path in this repository.
            for match in GITHUB_BLOB_LINK.finditer(line):
                target = match.group(1)
                if not (REPO_ROOT / target).exists():
                    failures.append(
                        f"{relative_name}:{line_number}: blob URL names '{target}', "
                        "which is not in the tree"
                    )

    assert not failures, "\n" + "\n".join(failures)
