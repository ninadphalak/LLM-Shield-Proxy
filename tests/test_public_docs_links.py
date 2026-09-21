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
RELATIVE_LINK = re.compile(r"\]\((\.{1,2}/[^)#?\s]*)(?:[#?][^)\s]*)?\)")
REPO_RELATIVE_LINK = re.compile(r"\]\((?!https?://|#|mailto:)([^)#?\s]+)(?:[#?][^)\s]*)?\)")
GITHUB_BLOB_LINK = re.compile(
    r"https://github\.com/ninadphalak/LLM-Shield-Proxy/(?:blob|tree)/main/([^)\s#?]+)"
)


def _doc_files() -> list[Path]:
    return sorted(
        path
        for suffix in DOC_SUFFIXES
        for path in DOCS_ROOT.rglob(f"*{suffix}")
        if path.is_file()
    )


def test_relative_doc_links_carry_their_file_extension() -> None:
    """An extensionless relative link resolves on the site and 404s on GitHub."""
    failures: list[str] = []
    for path in _doc_files():
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in RELATIVE_LINK.finditer(line):
                target = match.group(1)
                if target.endswith(DOC_SUFFIXES) or target.endswith("/"):
                    continue
                # A non-document asset keeps whatever extension it has; only a
                # bare page reference is the bug.
                if Path(target).suffix:
                    continue
                # Name the fix rather than hint at it: the suffix is whichever one
                # the target happens to have, and guessing .md is how an .mdx page
                # gets a link that still does not resolve.
                base = (path.parent / target).resolve()
                suffix = next(
                    (s for s in DOC_SUFFIXES if base.with_suffix(s).exists()), None
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
            for match in RELATIVE_LINK.finditer(line):
                target = match.group(1)
                if target.endswith("/"):
                    continue
                resolved = (path.parent / target).resolve()
                if not resolved.exists():
                    relative_path = path.relative_to(REPO_ROOT)
                    failures.append(f"{relative_path}:{line_number}: '{target}' does not exist")

    assert not failures, "\n" + "\n".join(failures)


def test_public_readme_links_into_the_repository_resolve() -> None:
    """Both READMEs pointed at `conformance/ci.md` after the page became `ci.mdx`."""
    failures: list[str] = []
    for relative_name in (*PUBLIC_ROOT_FILES, *PUBLIC_NESTED_FILES):
        path = REPO_ROOT / relative_name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            # A path relative to the file itself, as GitHub renders it.
            for match in REPO_RELATIVE_LINK.finditer(line):
                target = match.group(1)
                if not (path.parent / target).resolve().exists():
                    failures.append(f"{relative_name}:{line_number}: '{target}' does not exist")
            # An absolute blob/tree URL naming a path in this repository.
            for match in GITHUB_BLOB_LINK.finditer(line):
                target = match.group(1)
                if not (REPO_ROOT / target).exists():
                    failures.append(
                        f"{relative_name}:{line_number}: blob URL names '{target}', "
                        "which is not in the tree"
                    )

    assert not failures, "\n" + "\n".join(failures)
