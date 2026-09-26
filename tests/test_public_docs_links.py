"""Check that links and version pins in public docs point at something real.

Two failures this catches:

- A relative link with no file extension. Docusaurus rewrites it to a route, so
  the built site is fine, but it 404s when the page is read as a file on GitHub,
  which is where both READMEs send readers. `npm run build` says nothing about it.
- A version pin in the docs that does not match the version the package builds as.

Kept separate from `test_public_docs_style.py` because the conformance intake runs
that file by name before committing a results-wall row
(`scripts/process_conformance_submission.py`). Checks added there would fail a
stranger's submission over an unrelated broken link. `ci.yml` runs all of `tests/`,
so this module gates pull requests without touching intake.
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

# Matches `](target)` with or without a `./` prefix, with or without angle
# brackets, and with or without a title. The fragment is captured apart from the
# path so `./page#section` is checked too.
#
# A leading `/` is skipped: that is a site route, not a file path.
# Spaces are allowed only inside angle brackets, which is what that form is for.
# Title and fragment are defined once and shared with REFERENCE_DEFINITION below,
# so the two patterns cannot drift apart.
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

# A reference-style link: `[label]: destination`.
#
# Two lookalikes must not match. `[^1]: some prose` is a footnote, excluded by the
# label guard. `[Note]: this is prose` is not a definition, excluded by requiring
# what CommonMark requires: after the destination comes end of line or a title,
# nothing else.
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
    """Every doc file. Errors if there are none, rather than passing vacuously."""
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
                # A trailing slash already says "directory", so there is no
                # missing extension to report. The existence test covers it.
                if target.endswith("/"):
                    continue
                # A non-document asset keeps whatever extension it has; only a
                # bare page reference is the bug.
                if Path(target).suffix:
                    continue
                # A real file or directory with no extension is fine (LICENSE,
                # Makefile, a section folder). Only a bare page name is the bug.
                resolved = (path.parent / target).resolve()
                if resolved.is_file() or resolved.is_dir():
                    continue
                # Report the suffix the target actually has. Guessing .md sends
                # the author to write a link that still does not resolve.
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
            # Both link forms name a file, so both are checked.
            for pattern in (RELATIVE_LINK, REFERENCE_DEFINITION):
                for match in pattern.finditer(line):
                    target = _target(match)
                    # Trailing slash included: `](./nope/)` names a directory
                    # that does not exist and 404s like any missing file.
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
            # Fail closed: a rename or typo must not silently stop the check.
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


# A version pin in the docs: an Action `@benchmark-vX.Y.Z` or a `git+...@tag`.
# Only this shape. `pii-leak-benchmark>=0.3.0` is a floor, not a pin, and must
# not be flagged.
#
# Captures the whole tag, not just three numbers, so `benchmark-v0.4.1rc1` is
# reported as a mismatch instead of passing as `0.4.1`.
PINNED_BENCHMARK_TAG = re.compile(r"benchmark-v([\w.]+)")
BENCHMARK_ROOT = REPO_ROOT / "pii-leak-benchmark"
# The root README is not listed: it has a `>=` floor, not a pin.
VERSIONED_PUBLIC_FILES = (
    "examples/ci/gateway-pii-check.yml",
    "pii-leak-benchmark/README.md",
    "website/docs/conformance/ci.mdx",
    "website/docs/conformance/reproduce-fragmentation.md",
)


def _packaged_version() -> str:
    """The version the package will actually build as."""
    text = (BENCHMARK_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # Every match, not the first: a `version` key in another table would
    # otherwise supply the answer silently.
    found = re.findall(r"^version = \"([^\"]+)\"", text, re.MULTILINE)
    assert len(found) == 1, (
        f"expected exactly one top-level version in pii-leak-benchmark/pyproject.toml, "
        f"found {len(found)}: {found}"
    )
    return found[0]


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
    """Catch a partial bump: pyproject.toml moves but a doc still names the old tag.

    Compares files only. Says nothing about whether the tag exists on GitHub: the
    release order is merge first, then tag from the merge commit, so at merge time
    the tag correctly does not exist yet.
    """
    expected = _packaged_version()
    failures: list[str] = []
    for relative_name in VERSIONED_PUBLIC_FILES:
        path = REPO_ROOT / relative_name
        if not path.exists():
            failures.append(f"{relative_name}: listed as version-bearing but not in the tree")
            continue
        seen_here = 0
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in PINNED_BENCHMARK_TAG.finditer(line):
                seen_here += 1
                if match.group(1) != expected:
                    failures.append(
                        f"{relative_name}:{line_number}: pins benchmark-v{match.group(1)}, "
                        f"but the package builds as {expected}"
                    )
        # Count per file, not once overall, so one file going unread cannot be
        # covered by matches in another. An exact total would be worse: it would
        # fail whenever someone legitimately adds a pin.
        if not seen_here:
            failures.append(
                f"{relative_name}: listed as version-bearing but no pinned tag was found in it, "
                "so this file is no longer being checked"
            )

    assert not failures, "\n" + "\n".join(failures)
