"""Keep recurring plain-language problems out of public documentation."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "STABILITY.md",
    "LIMITATIONS.md",
)
PUBLIC_DIRECTORIES = (
    REPO_ROOT / "benchmarks",
    REPO_ROOT / "pii-leak-benchmark",
    REPO_ROOT / "website" / "blog",
    REPO_ROOT / "website" / "docs",
    REPO_ROOT / "website" / "src",
)
PUBLIC_SUFFIXES = {".md", ".mdx", ".tsx"}
# The results wall keeps its rows in data files rather than in the page, and both of them
# are published prose the moment the site builds: `results-wall.ts` holds the rows this
# project measured, `submitted-rows.json` holds the ones a workflow writes from an issue
# anyone can open. Neither suffix was in the set above, so neither was checked by
# anything, and an em dash in a row's "What happened" column shipped.
#
# Scoped to this directory rather than added to the suffix set, deliberately. `.json`
# across the whole tree would sweep the frozen evidence under `benchmarks/results/`, which
# is reproduction input rather than prose and must not be edited to satisfy a style rule.
PUBLIC_DATA_DIRECTORY = REPO_ROOT / "website" / "src" / "data"
PUBLIC_DATA_SUFFIXES = {".ts", ".json"}
BANNED_TEXT = {
    "\N{EM DASH}": "Use a short sentence, colon, comma, or hyphen instead of an em dash.",
    "reference implementation": "Name LLM-Shield-Proxy directly.",
    "protected data reached the capture": "Say that the gateway sent an unmasked test value to the capture server.",
    "first one matters more": "Describe the two packages without ranking them.",
    "row carries the same": "State the result and its status directly.",
    "roughly 35-line": "Describe the fixed-format limitation without an incidental line count.",
}


def _public_files() -> list[Path]:
    files = [REPO_ROOT / relative_path for relative_path in PUBLIC_ROOT_FILES]
    for directory in PUBLIC_DIRECTORIES:
        files.extend(
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in PUBLIC_SUFFIXES
        )
    files.extend(
        path
        for path in PUBLIC_DATA_DIRECTORY.rglob("*")
        if path.is_file() and path.suffix.lower() in PUBLIC_DATA_SUFFIXES
    )
    return sorted(set(files))


def test_public_docs_avoid_known_plain_language_regressions() -> None:
    failures: list[str] = []
    for path in _public_files():
        text = path.read_text(encoding="utf-8")
        for banned, guidance in BANNED_TEXT.items():
            if banned.casefold() in text.casefold():
                relative_path = path.relative_to(REPO_ROOT)
                failures.append(f"{relative_path}: {banned!r}. {guidance}")

    assert not failures, "\n" + "\n".join(failures)
