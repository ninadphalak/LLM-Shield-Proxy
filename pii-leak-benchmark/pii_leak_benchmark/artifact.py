"""Writing a conformance report to disk. Standard library only, on purpose.

This lived in ``local``, which imports the reference proxy's detector, vault and
streaming engines. The HTTP profile needs to write its report too, so every
``benchmark --target-base-url`` run imported the entire proxy in order to call
``json.dumps`` and open a file.

Nothing in this distribution may import from ``llm_shield_proxy``. The benchmark is the
neutral measurer; the proxy is one of the things it measures.
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


def write_json_artifact(
    destination: Path | str, payload: Any, *, indent: int = 1
) -> Path:
    """Write one JSON evidence file canonically: UTF-8, LF endings, trailing newline.

    The hazard `write_conformance_report` documents above, factored out because it was
    fixed once for v1 and then reintroduced by every writer added since: the v2 emitter,
    the FIDE emitter and three sweep drivers each called `Path.write_text`, whose text
    mode rewrites the newline to CRLF on Windows and so makes an artifact's SHA-256
    depend on the host that produced it rather than on the measurement.

    `indent` stays a parameter and keys are NOT sorted, because v1 publishes
    `indent=2, sort_keys=True` while v2 and FIDE publish `indent=1` in insertion order.
    This writer fixes the byte-level encoding; it deliberately does not unify the two
    published shapes, which would change every future report's diff against the frozen
    round-eight tree for no measurement reason.

    The write is atomic. A sibling temporary is replaced into position, so an
    interrupted sweep cannot leave a half-written report where a valid one used to be.
    """
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, indent=indent))
        handle.write("\n")
    temporary.replace(target)
    return target
