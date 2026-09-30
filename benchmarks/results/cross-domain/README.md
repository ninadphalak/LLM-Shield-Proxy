# Cross-domain split-boundary measurements

Every-split runs of streaming redactors, secret scanners and log pipelines that have nothing to
do with LLMs, measured 2026-09-30 on Docker 29.7.2, Node 24.20.0 and Python 3.14.7 (Windows 11
host). The question: does a filter that sees a stream one chunk at a time miss a value that
straddles two chunks, or forward one piece of it?

Method, the same as the LLM rows: one fixed text per value, the whole text once, then every
single cut from 8 bytes before the value to 8 bytes after it. One leaking offset is a leak.
Values: an email (20 chars), a US SSN (11), a 2048-bit RSA private key in PEM (1,678) and, where
a bound above 2 KB matters, a 4096-bit key (3,242). Keys are generated per run by
`common/gen_fixtures.js`; the fixtures file is never committed. Reports store outputs with the
value masked.

Leak classes: `whole` (value forwarded verbatim), `fragment` (a prefix or suffix of 4+
characters survived while the rest was redacted: the filter fired on one piece), `none`.

| Directory | Target | Result |
|---|---|---|
| `replacestream/` | npm replacestream 4.0.3, in-process | PEM leaks whole at every cut past 100 chars into the key (1,577 of 1,695 cuts); clean with `maxMatchLen` raised |
| `proxy-py/` | proxy.py 2.4.10 `handle_upstream_chunk`, reference plugin, over the wire | see `report.json` |
| `openresty/` | OpenResty `body_filter_by_lua_block`, reference recipe, over the wire | see `report-*.json` |
| `caddy/` | Caddy replace-response stream mode (icholy/replace, MaxMatchSize 2048) | see `report.json` |
| `nginx-sub-filter/` | nginx `sub_filter` literals | see `report.json` |
| `scanners/` | gitleaks 8.30.1 and trufflehog 3.97.9 on files built around their chunk edges | gitleaks `dir` misses a key across its 25,000-byte extension cap; trufflehog misses a key longer than its 3,072-byte peek |
| `fluent-bit-docker/` | Docker json-file 16 KiB split + Fluent Bit 5.1.2 tail + lua redaction | leaks in both fragments without the docker multiline parser; clean with it |
| `gitlab-runner/` | GitLab Runner 19.4.1 job-log masker, in-process | clean, 0 of 1,760 |
| `buildkite-agent/` | Buildkite agent 4.0.9 redactor, in-process | clean, 0 of 1,760 |
| `aho-corasick/` | Rust aho-corasick 1.1.5 `try_stream_replace_all`, in-process | clean, 0 of 1,760 |

`common/` holds the origin server that cuts the response body on the wire
(`split_upstream.py`), the every-split HTTP client (`wire_client.py`), the patterns and the
leak classifier. Each target directory has its own README with the exact versions, digests,
configuration and the command that produced its report.
