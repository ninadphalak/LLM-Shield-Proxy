"""Reference redaction plugin for proxy.py: the shortest thing a user would write.

`handle_upstream_chunk` is called once per raw upstream read (proxy/http/proxy/plugin.py:130-138
in 2.4.10). This plugin applies the three patterns to each chunk on its own, which is what the
API invites; it keeps no state between calls.
"""
import json
import os
import re
from typing import Optional

from proxy.http.proxy import HttpProxyBasePlugin

with open(os.environ["XD_PATTERNS"], encoding="utf-8") as f:
    _PATTERNS = [re.compile(p.encode("utf-8")) for p in json.load(f).values()]


class RedactPerChunkPlugin(HttpProxyBasePlugin):
    def handle_upstream_chunk(self, chunk: memoryview) -> Optional[memoryview]:
        data = chunk.tobytes()
        for pat in _PATTERNS:
            data = pat.sub(b"[REDACTED]", data)
        return memoryview(data)
