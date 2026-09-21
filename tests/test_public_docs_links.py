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
# Angle brackets and titles are handled once, shared by both patterns below.
# They drifted apart otherwise: the inline form accepted only a double-quoted
# title while the reference form accepted all three, so the same link checked or
# escaped depending on which way it was written. A destination containing a space
# is legal only inside angle brackets, which is the whole reason the form exists,
# so the bracketed branch allows one and the bare branch does not.
_NOT_A_FILE_PATH = r"(?!https?://|\#|mailto:|/)"
_TITLE = r"""(?:[ 	]+(?:"[^"]*"|'[^']*'|\([^)]*\)))?"""
_FRAGMENT = r"(?:[\#?][^)>\s]*)?"

RELATIVE_LINK = re.compile(
    rf"""\]\([ 	]*
        (?:
            <{_NOT_A_FILE_PATH}([^>
]+?){_FRAGMENT}>     # bracketed, spaces legal
          | {_NOT_A_FILE_PATH}([^)<>\#?\s]+){_FRAGMENT}  # bare
        )
        {_TITLE}[ 	]*\)""",
    re.VERBOSE,
)
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
    rf"""^\[(?!\^)[^\]]+\]:[ 	]*
        (?:
            <{_NOT_A_FILE_PATH}([^>
]+?){_FRAGMENT}>
          | {_NOT_A_FILE_PATH}([^<>\s\#?]+){_FRAGMENT}
        )
        {_TITLE}[ 	]*$""",
    re.MULTILINE | re.VERBOSE,
)


# Restricted to refs that describe the current tree. A link pinned to a tag names a
# path as it was then, so checking that against today's files would be wrong.
GITHUB_BLOB_LINK = re.compile(
    r"https://(?:github\.com/ninadphalak/LLM-Shield-Proxy/(?:blob|tree)"
    r"|raw\.githubusercontent\.com/ninadphalak/LLM-Shield-Proxy)"
    r"/(?:main|HEAD)/([^)\s#?]+)"
)


def _target(match: "re.Match[str]") -> str:
    """The destination, from whichever of the two branches matched."""
    return next(group for group in match.groups() if group is not None)


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
                target = _target(match)
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
                    target = _target(match)
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
                    target = _target(match)
                    if not (path.parent / target).resolve().exists():
                        failures.append(
                            f"{relative_name}:{line_number}: '{target}' does not exist"
                        )
            # An absolute blob/tree URL naming a path in this repository.
            for match in GITHUB_BLOB_LINK.finditer(line):
                target = _target(match)
                if not (REPO_ROOT / target).exists():
                    failures.append(
                        f"{relative_name}:{line_number}: blob URL names '{target}', "
                        "which is not in the tree"
                    )

    assert not failures, "\n" + "\n".join(failures)


# The version the docs tell a reader to install, as a tag on a pinned Action or a
# `git+...@tag` source. Only this exact shape: `pii-leak-benchmark>=0.3.0` is a
# deliberate floor, not a claim about the current release, and flagging one would
# teach people to stop trusting this test.
PINNED_BENCHMARK_TAG = re.compile(r"benchmark-v(\d+\.\d+\.\d+)")
BENCHMARK_ROOT = REPO_ROOT / "pii-leak-benchmark"
VERSIONED_PUBLIC_FILES = (
    "README.md",
    "examples/ci/gateway-pii-check.yml",
    "pii-leak-benchmark/README.md",
    "website/docs/conformance/ci.mdx",
    "website/docs/conformance/reproduce-fragmentation.md",
)


def _packaged_version() -> str:
    """The version the package will actually build as."""
    text = (BENCHMARK_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r"^version = \"([^\"]+)\"", text, re.MULTILINE)
    assert match, "no version in pii-leak-benchmark/pyproject.toml"
    return match.group(1)


def test_the_packaged_version_and_the_importable_one_agree() -> None:
    """Two files carry the version, so they can disagree, and a wheel would ship both."""
    init = (BENCHMARK_ROOT / "pii_leak_benchmark" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r"^__version__ = \"([^\"]+)\"", init, re.MULTILINE)
    assert match, "no __version__ in pii_leak_benchmark/__init__.py"
    assert match.group(1) == _packaged_version(), (
        f"__init__.py says {match.group(1)} and pyproject.toml says {_packaged_version()}. "
        "A release bump has to move both."
    )


def test_docs_pin_the_version_that_is_being_released() -> None:
    """A stale pin sends a reader to a tag that does not exist.

    The failure this catches is a partial bump: `pyproject.toml` moves and one of
    the eight places the docs name the tag does not, so a copied command installs
    the wrong version or 404s. It compares files only, and deliberately says
    nothing about whether the tag has been cut yet, because the release order here
    is to merge first and tag from the merge commit. A test demanding the tag
    exist would fail every release pull request for the wrong reason.
    """
    expected = _packaged_version()
    failures: list[str] = []
    seen = 0
    for relative_name in VERSIONED_PUBLIC_FILES:
        path = REPO_ROOT / relative_name
        if not path.exists():
            failures.append(f"{relative_name}: listed as version-bearing but not in the tree")
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in PINNED_BENCHMARK_TAG.finditer(line):
                seen += 1
                if match.group(1) != expected:
                    failures.append(
                        f"{relative_name}:{line_number}: pins benchmark-v{match.group(1)}, "
                        f"but the package builds as {expected}"
                    )

    assert not failures, "\n" + "\n".join(failures)
    # Fail closed, as elsewhere in this module. Finding no pins at all means the
    # pattern or the file list has gone stale, not that everything is consistent.
    assert seen, "no pinned benchmark tag found in any listed file; this check went blind"
