"""Submitter-supplied link targets must never reach an `href` unvalidated.

The results wall and the corpus credits exist to render rows other people submit. React
escapes text but does NOT validate URLs, so a `javascript:` target interpolated into an
`href` executes on click, and React only warns about it in development. On a page whose
subject is other people's security defects, that is the one bug that must not ship.

This is a source-level guard rather than a behavioural test because the site has no JS
test runner. It is deliberately crude: it fails if any `href={...}` in these components
is not wrapped in `safeHref(...)`, which is a rule a reviewer can also apply by eye.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Components that render third-party submitted data.
SUBMITTER_FACING = (
    ROOT / "website" / "src" / "components" / "ResultsWall" / "index.tsx",
    ROOT / "website" / "src" / "components" / "CorpusCredits" / "index.tsx",
)

HREF = re.compile(r"href=\{([^}]*)\}")


ASSIGNED_FROM_GUARD = re.compile(r"(?:const|let)\s+(\w+)\s*=\s*safeHref\(")


@pytest.mark.parametrize("path", SUBMITTER_FACING, ids=lambda p: p.parent.name)
def test_every_href_expression_is_validated(path: pathlib.Path) -> None:
    source = path.read_text(encoding="utf-8")
    # A component may call the guard inline, or bind its result to a name and use that.
    # Both are fine; anything else is a raw submitter value reaching the DOM.
    validated_names = set(ASSIGNED_FROM_GUARD.findall(source))
    unguarded = [
        expression.strip()
        for expression in HREF.findall(source)
        if "safeHref(" not in expression and expression.strip() not in validated_names
    ]
    assert not unguarded, (
        f"{path.name} interpolates an unvalidated link target into href: {unguarded}. "
        "Wrap it in safeHref() from website/src/utils/safeHref.ts, which drops any "
        "scheme that is not http, https or mailto."
    )


@pytest.mark.parametrize("path", SUBMITTER_FACING, ids=lambda p: p.parent.name)
def test_the_component_imports_the_guard(path: pathlib.Path) -> None:
    """A component with no links today still gets the import checked, so removing the
    last link does not quietly remove the protection for the next one added."""
    source = path.read_text(encoding="utf-8")
    assert "safeHref" in source


def test_the_guard_rejects_the_schemes_that_execute() -> None:
    """The blocklist is the point, so it is pinned here in the language that reads it.

    Kept as a source assertion rather than a JS execution test: the site ships no test
    runner, and a silently deleted scheme would otherwise be invisible until someone
    submitted a row that used it.
    """
    guard = (ROOT / "website" / "src" / "utils" / "safeHref.ts").read_text(encoding="utf-8")
    # Only these three may be allowed through.
    allowed = re.search(r"SAFE_SCHEMES = new Set\(\[([^\]]*)\]\)", guard)
    assert allowed, "the allowlist moved; this guard needs updating with it"
    schemes = {value.strip().strip("'\"") for value in allowed.group(1).split(",") if value.strip()}
    assert schemes == {"http", "https", "mailto"}, schemes

    # Tab, newline and carriage return must be removed BEFORE the scheme is read. A
    # browser ignores them anywhere in a URL, so `java<TAB>script:` reads as a scheme of
    # `java` to a checker that looks first and executes as `javascript:` in the browser.
    strip_index = guard.index("\\u0009")
    scheme_index = guard.index("SCHEME.exec(")
    assert strip_index < scheme_index

    # Interior spaces must NOT be stripped, only trimmed at the ends. Removing one
    # rewrites a legitimate link to a different destination. The trim is anchored; the
    # unanchored removal covers only the three characters above.
    assert "^[\\u0000-\\u0020]+|[\\u0000-\\u0020]+$" in guard
