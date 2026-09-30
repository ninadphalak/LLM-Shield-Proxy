"""Builds scan targets where a private key straddles the scanner's chunk edge.

python build_files.py <fixtures.json> <out_dir>

gitleaks 8.30.1 (sources/file.go): 100,000-byte reads, each extended until two consecutive
newlines are found or 25,000 more bytes were read; consecutive chunks do not overlap.
trufflehog 3.97.9 (pkg/sources/chunker.go): 10,240-byte chunks, each extended by a 3,072-byte
peek into the following bytes (so a value up to 3,072 bytes always fits in one chunk).

Filler is 79 'x' characters plus a newline per line: no blank line anywhere, so the gitleaks
boundary search runs to its 25,000-byte cap and then cuts mid-line.
"""
import json
import os
import sys

fixtures = json.load(open(sys.argv[1], encoding="utf-8"))
out = sys.argv[2]
os.makedirs(out, exist_ok=True)
LINE = b"x" * 79 + b"\n"


def build(name, key, start, total):
    """Key at byte offset `start` inside `total` bytes of filler lines."""
    body = bytearray(LINE * (total // len(LINE) + 1))
    body[start : start + len(key)] = key
    body = bytes(body[:total])
    with open(os.path.join(out, name), "wb") as f:
        f.write(body)
    assert body.find(key) == start
    return {"file": name, "key_start": start, "key_end": start + len(key), "size": total}


pem2048 = fixtures["pem2048"].encode()
pem4096 = fixtures["pem4096"].encode()
plan = {
    "gitleaks": [
        # control: key well inside the first read
        build("gitleaks_control_inside_first_read.txt", pem2048, 50_000, 300_000),
        # key straddles 100,000 and also 125,000 (the 25,000-byte extension cap): 124,000..125,678
        build("gitleaks_straddle_extension_cap.txt", pem2048, 124_000, 300_000),
        # key straddles 100,000 only; the extension window (to 125,000) still covers it: expected found
        build("gitleaks_straddle_read_edge_only.txt", pem2048, 99_500, 300_000),
    ],
    "trufflehog": [
        build("trufflehog_control_inside_chunk.txt", pem4096, 5_000, 40_960),
        # 2048-bit key (1,678 bytes) across 10,240: the 3,072-byte peek covers it, expected found
        build("trufflehog_straddle_covered_by_peek.txt", pem2048, 10_100, 40_960),
        # 4096-bit key (3,242 bytes) from 10,100 to 13,342: past chunk end 10,240 and peek end 13,312
        build("trufflehog_straddle_past_peek.txt", pem4096, 10_100, 40_960),
    ],
}
json.dump(plan, open(os.path.join(out, "plan.json"), "w"), indent=1)
print(json.dumps(plan, indent=1))
