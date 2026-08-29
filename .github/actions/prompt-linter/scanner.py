"""LLM-Shield Prompt Template Linter.

Standalone (stdlib-only) Tier-1 regex and Tier-2 Shannon-entropy scanner for
CI-time PII/secret linting of prompt template files (.txt/.md), mirroring the
detection heuristics used by LLM-Shield-Proxy's runtime redaction engine.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter
from pathlib import Path

_SKIP_DIR_NAMES = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "dist", "build"}

# Tier 1: deterministic, structured PII/secret patterns.
_TIER1_PATTERNS: dict[str, re.Pattern] = {
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "AWS_ACCESS_KEY": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    "IPV4": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "GENERIC_API_KEY_ASSIGNMENT": re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}['\"]?"
    ),
    "PRIVATE_KEY_HEADER": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}

_ENTROPY_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9+/_=\-]{16,}")


def calculate_shannon_entropy(text: str) -> float:
    """Shannon entropy in bits/symbol: H(S) = -sum(p(x) * log2(p(x)))."""
    if not text:
        return 0.0
    length = len(text)
    counts = Counter(text)
    entropy = 0.0
    for count in counts.values():
        p = count / length
        entropy -= p * math.log2(p)
    return entropy


def iter_target_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".txt", ".md"):
            continue
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        yield path


def scan_file(path: Path, entropy_threshold: float, min_token_length: int) -> list[dict]:
    findings = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        return [{"line": 1, "tier": "IO", "detail": f"Could not read file: {exc}"}]

    for line_no, line in enumerate(lines, start=1):
        for entity_type, pattern in _TIER1_PATTERNS.items():
            if pattern.search(line):
                findings.append({"line": line_no, "tier": "TIER1_REGEX", "detail": f"Matched {entity_type} pattern"})

        for match in _ENTROPY_TOKEN_PATTERN.finditer(line):
            token = match.group(0)
            if len(token) < min_token_length:
                continue
            entropy = calculate_shannon_entropy(token)
            if entropy >= entropy_threshold:
                findings.append(
                    {
                        "line": line_no,
                        "tier": "TIER2_ENTROPY",
                        "detail": f"High-entropy token ({entropy:.2f} bits/symbol >= {entropy_threshold})",
                    }
                )

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM-Shield Prompt Template Linter")
    parser.add_argument("--path", default=".", help="Root path to scan for .txt/.md files")
    parser.add_argument("--entropy-threshold", type=float, default=4.5, help="Shannon entropy threshold (bits/symbol)")
    parser.add_argument("--min-token-length", type=int, default=16, help="Minimum token length for Tier-2 analysis")
    parser.add_argument(
        "--fail-on-finding",
        type=str,
        default="true",
        help="If 'true', exit non-zero when any finding is detected",
    )
    args = parser.parse_args()

    root = Path(args.path).resolve()
    total_findings = 0

    for file_path in sorted(iter_target_files(root)):
        findings = scan_file(file_path, args.entropy_threshold, args.min_token_length)
        rel_path = file_path.relative_to(root) if file_path.is_relative_to(root) else file_path
        for finding in findings:
            total_findings += 1
            print(
                f"::error file={rel_path},line={finding['line']}::"
                f"[{finding['tier']}] {finding['detail']}"
            )

    if total_findings == 0:
        print(f"Prompt-linter: scanned {root} — no Tier-1/Tier-2 findings.")
        return 0

    print(f"Prompt-linter: {total_findings} finding(s) detected under {root}.")
    fail_on_finding = args.fail_on_finding.strip().lower() in ("1", "true", "yes")
    return 1 if fail_on_finding else 0


if __name__ == "__main__":
    sys.exit(main())
