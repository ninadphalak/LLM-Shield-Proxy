"""The response-split profile needs a setting the quickstart does not set; say so where people look.

Found in the clean-install audit: the README trial's proxy, measured with
`pii-leak-benchmark-v2`, reported a leak on the reply path with fidelity 0.5. The request
path was clean. The cause was `ENABLE_RESPONSE_PII_REDACTION`, off by default and named in
no public document, so a newcomer had no way to know what to change. It stays off by default
(it changes what callers receive); the fix is that every page that leads someone to the
response profile names the setting, and the default the pages describe is the real one.
"""

from __future__ import annotations

from pathlib import Path

from llm_shield_proxy.core.config import Settings

ROOT = Path(__file__).resolve().parents[1]
SETTING = "ENABLE_RESPONSE_PII_REDACTION"

PAGES_THAT_MUST_NAME_IT = [
    ROOT / ".env.example",
    ROOT / "website" / "docs" / "deployment.md",
    ROOT / "website" / "docs" / "conformance" / "ci.mdx",
    ROOT / "pii-leak-benchmark" / "README.md",
]


def test_the_setting_is_off_by_default_as_the_pages_say() -> None:
    assert Settings.model_fields[SETTING].default is False


def test_every_page_that_leads_to_the_response_profile_names_the_setting() -> None:
    missing = [str(p.relative_to(ROOT)) for p in PAGES_THAT_MUST_NAME_IT if SETTING not in p.read_text(encoding="utf-8")]
    assert not missing, f"{SETTING} is not named in: {missing}"


def test_the_ci_guide_names_it_beside_the_response_profile() -> None:
    """Next to `profiles: both`, not somewhere else on a long page."""
    text = (ROOT / "website" / "docs" / "conformance" / "ci.mdx").read_text(encoding="utf-8")
    section = text.split("### Run the response-split profile as well", 1)[1].split("\n<details>", 1)[0]
    assert SETTING in section
