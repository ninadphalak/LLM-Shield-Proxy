"""Put the library's source on the path so these tests need no install step.

``chunk-invariance`` is its own distribution (``chunk-invariance/``), like the harness and the
MCP checker. Nothing in it imports the gateway.
"""

import sys
from pathlib import Path

LIBRARY_DIST = Path(__file__).resolve().parents[2] / "chunk-invariance"
if str(LIBRARY_DIST) not in sys.path:
    sys.path.insert(0, str(LIBRARY_DIST))
