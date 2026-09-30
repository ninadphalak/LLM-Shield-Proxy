"""``pii-leak-benchmark-v2`` -- the v2 response-split profile as a console command.

The same parser and the same run as ``python -m pii_leak_benchmark.v2_emitter``, under the
command's own name. It works from any directory: ``--validate`` reads the schema bundled
with the package, not a path relative to the repository root.

Requires only the standard library and ``httpx``; ``--validate`` needs the ``validate``
extra (``pip install "pii-leak-benchmark[validate]"``).
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from pii_leak_benchmark import v2_emitter

PROG = "pii-leak-benchmark-v2"


def build_parser() -> argparse.ArgumentParser:
    return v2_emitter.build_parser(prog=PROG)


def main(argv: Optional[Sequence[str]] = None) -> int:
    return v2_emitter.main(list(sys.argv[1:] if argv is None else argv), prog=PROG)


if __name__ == "__main__":
    sys.exit(main())
