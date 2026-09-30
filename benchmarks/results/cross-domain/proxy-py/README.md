# proxy.py 2.4.10 `handle_upstream_chunk` with a reference redaction plugin

The hook is per raw upstream read by design (`proxy/http/proxy/plugin.py:130-138`: "Handler
called right after receiving raw response from upstream server ... Return None if you don't want
to sent this chunk to the client"). `redact_plugin.py` is the shortest plugin a user would write:
`re.sub` of the three patterns on each chunk, no state. The measurement shows what the hook
delivers when the origin splits the body on the wire.

- `proxy.py==2.4.10` from PyPI in a Python 3.14.7 venv, started with
  `python -m proxy --hostname 127.0.0.1 --port 8899 --plugins redact_plugin.RedactPerChunkPlugin`
  and `XD_PATTERNS=../common/patterns.json`.
- Origin: `common/split_upstream.py` on the host, 60 ms pause between the two writes.
- Client: `common/wire_client.py --proxy http://127.0.0.1:8899 --origin http://127.0.0.1:18080`.

| Case | Whole | Cuts tried | Leaking | Leak classes | Leaking cuts (into the value) |
|---|---|---|---|---|---|
| email (20) | none | 37 | 15 | 11 whole, 4 fragment | 4 to 18 |
| ssn (11) | none | 28 | 10 | 10 whole | 1 to 10 |
| pem2048 (1,678) | none | 1,695 | 1,677 | 1,677 whole | 1 to 1,677 |

Reading: every cut inside the SSN and the key forwards the value whole; for the email, cuts in
the local part leave a fragment (`jane[REDACTED]`) and cuts elsewhere forward it whole. The
plugin sees exactly the boundaries the origin chose. Cuts outside the value never leak. The
first chunk also carries the response headers, which is why a Content-Length mismatch after a
replacement is the client's problem in this design (the client tolerates the short read).

Fix pattern for plugin authors: keep a bounded tail (longer than the longest possible match)
and rescan it with the next chunk; flush it when the upstream closes.

Reproduce: see the commands above; `report.json` is the transcript with the value masked.
