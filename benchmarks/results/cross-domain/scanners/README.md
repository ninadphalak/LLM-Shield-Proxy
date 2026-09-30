# gitleaks 8.30.1 and trufflehog 3.97.9: a key across the chunk edge

Detectors, not forwarders: nothing is emitted, but the scan unit is a chunk chosen by the reader,
so a value that straddles two chunks can be missed. `build_files.py` places a real PEM key at
byte offsets computed from each scanner's reader; `report.json` is the sanitized outcome (counts,
rule names, line numbers; never the match text). Filler is 79 `x` plus newline per line, no blank
line anywhere.

Images (pulled 2026-09-30):

- `ghcr.io/gitleaks/gitleaks:v8.30.1` digest `sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f`
- `trufflesecurity/trufflehog:3.97.9` digest `sha256:52e67fef4d054ecff5c2ce4b4ae376626d1ef54aa0898b53cac19c25e92e14db`

## gitleaks (`sources/file.go:21,180,210-212`; `sources/common.go:16,58-110`)

Reads 100,000 bytes, then extends until two consecutive newlines appear or 25,000 more bytes
were read, then cuts wherever it is. No overlap between chunks.

| File (300,000 bytes) | Key (1,678 bytes) at | `gitleaks dir` | `gitleaks stdin` |
|---|---|---|---|
| control_inside_first_read | 50,000 | 1 finding (private-key) | 1 finding |
| straddle_read_edge_only | 99,500 to 101,178: past the 100,000 read, inside the extension window | 1 finding | 1 finding |
| straddle_extension_cap | 124,000 to 125,678: past the 125,000 extension cap | **0 findings** | 1 finding |

Commands:

```
gitleaks dir /data/<file> --no-banner --exit-code 0 --report-format json --report-path /data/<file>.json
gitleaks stdin ... < <file>
```

`stdin` found the capped case in this run: a pipe hands the reader smaller pieces than the
100,000-byte buffer, so its edges fall elsewhere for this file. It is the same reader; the
position of the miss depends on the read sizes the OS returns, not on whether the miss exists.

## trufflehog (`pkg/sources/chunker.go:13-18`, `readInChunks`; `pkg/handlers/default.go:41`)

10,240-byte chunks, each extended by a 3,072-byte peek of the following bytes. A value up to
3,072 bytes therefore always fits in one chunk; a longer one can straddle both edges.

| File (40,960 bytes) | Key at | Findings |
|---|---|---|
| control_inside_chunk | 4096-bit key (3,242 bytes) at 5,000 | 1 (PrivateKey) |
| straddle_covered_by_peek | 2048-bit key (1,678) at 10,100 to 11,778, inside the peek | 1 (PrivateKey) |
| straddle_past_peek | 4096-bit key at 10,100 to 13,342, past the peek end 13,312 | **0** |

Command: `trufflehog filesystem /data/<file> --no-verification --json --no-update`.

Reproduce: `python build_files.py fixtures.json <dir>` then the commands above with `<dir>`
mounted at `/data`.
