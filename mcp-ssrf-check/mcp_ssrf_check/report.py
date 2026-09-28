"""Check results and their two renderings: a JSON file and a terminal table."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

SCHEMA = "mcp-ssrf-check/1"

PASS = "pass"  # nosec B105 - a status label, not a credential
FAIL = "fail"
SKIP = "skip"
INFO = "info"
INCONCLUSIVE = "inconclusive"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_INCONCLUSIVE = 2

# Characters CommonMark lets a backslash escape, which covers table pipes, links, images,
# HTML tags, emphasis and code spans.
_MARKDOWN_SPECIAL = set("\\`*_{}[]()<>#+-.!|~&\"'")


@dataclass
class CheckResult:
    id: str
    title: str
    status: str
    detail: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    reference: Optional[str] = None


@dataclass
class Report:
    target: str
    lifecycle: Optional[str]
    protocol_version: Optional[str]
    checks: List[CheckResult]
    version: str
    generated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "tool": {"name": "mcp-ssrf-check", "version": self.version},
            "generated_at": self.generated_at,
            "target": self.target,
            "lifecycle": self.lifecycle,
            "protocol_version": self.protocol_version,
            "checks": [asdict(c) for c in self.checks],
            "summary": self.summary(),
        }

    def summary(self) -> Dict[str, int]:
        counts = {PASS: 0, FAIL: 0, SKIP: 0, INFO: 0, INCONCLUSIVE: 0}
        for check in self.checks:
            counts[check.status] = counts.get(check.status, 0) + 1
        return counts

    def exit_code(self) -> int:
        statuses = {c.status for c in self.checks}
        if FAIL in statuses:
            return EXIT_FAIL
        if INCONCLUSIVE in statuses:
            return EXIT_INCONCLUSIVE
        return EXIT_OK

    def write_json(self, path: str) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True)
            fh.write("\n")

    def render_markdown(self) -> str:
        """The same table as ``render_text``, for a CI job summary such as ``$GITHUB_STEP_SUMMARY``.

        The target's protocol version, and details that quote it, come from the server under test,
        so every interpolated string is escaped: it can neither leave its cell nor render as a link,
        an image, HTML or emphasis.
        """

        def cell(text: str) -> str:
            text = " ".join(text.splitlines())
            return "".join("\\" + ch if ch in _MARKDOWN_SPECIAL else ch for ch in text)

        counts = self.summary()
        lines = [
            f"### mcp-ssrf-check {cell(self.version)}",
            "",
            f"Target {cell(self.target)}, lifecycle {cell(self.lifecycle or 'unknown')} "
            f"({cell(self.protocol_version or 'no version negotiated')}).",
            "",
            "| Result | Check | Detail |",
            "| :--- | :--- | :--- |",
        ]
        for check in self.checks:
            lines.append(f"| {cell(check.status.upper())} | {cell(check.id)} | {cell(check.detail)} |")
        lines.append("")
        lines.append("Summary: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v) + ".")
        return "\n".join(lines) + "\n"

    def write_markdown(self, path: str) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self.render_markdown())

    def render_text(self) -> str:
        width = max([len(c.id) for c in self.checks] + [8])
        lines = [
            f"mcp-ssrf-check {self.version}",
            f"target: {self.target}",
            f"lifecycle: {self.lifecycle or 'unknown'} ({self.protocol_version or 'no version negotiated'})",
            "",
        ]
        for check in self.checks:
            lines.append(f"{check.status.upper():<13}{check.id:<{width + 2}}{check.detail}")
        counts = self.summary()
        lines.append("")
        lines.append(
            "summary: "
            + ", ".join(f"{k} {v}" for k, v in counts.items() if v)
            + (".  A FAIL is a finding on the server you pointed this at." if counts[FAIL] else ".")
        )
        return "\n".join(lines)
