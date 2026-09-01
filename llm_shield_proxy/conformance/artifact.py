"""Writing a conformance report to disk. Standard library only, on purpose.

This lived in ``local``, which imports the reference proxy's detector, vault and
streaming engines. The HTTP profile needs to write its report too, so every
``benchmark --target-base-url`` run imported the entire proxy in order to call
``json.dumps`` and open a file.

Nothing here may import from ``llm_shield_proxy`` outside ``conformance``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_conformance_report(report: dict[str, Any], output_path: str) -> str:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Explicit LF: Path.write_text uses text mode, which rewrites newlines to CRLF
    # on Windows and makes the published SHA-256 of an artifact platform-dependent.
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
    return str(destination)
