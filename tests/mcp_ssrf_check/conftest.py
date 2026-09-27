"""Put the checker's source on the path so these tests need no install step.

The checker is its own distribution (``mcp-ssrf-check/``), like the harness, and CI installs
only the harness. Importing from source here keeps the dependency direction visible: nothing
in the checker imports the gateway, and the gateway's conftest is not needed to run it.
"""

import sys
from pathlib import Path

CHECKER_DIST = Path(__file__).resolve().parents[2] / "mcp-ssrf-check"
if str(CHECKER_DIST) not in sys.path:
    sys.path.insert(0, str(CHECKER_DIST))
