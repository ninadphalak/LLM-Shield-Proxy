"""One stdout line of about 20,000 bytes with the 2048-bit key straddling byte 16,384, the
json-file log driver's split point (moby daemon/logger/copier.go:22, defaultBufSize 16 KiB).

python make_line.py <fixtures.json> <out_dir>   -> <out_dir>/line.txt and <out_dir>/plan.json
"""
import json
import os
import sys

fx = json.load(open(sys.argv[1], encoding="utf-8"))
key = fx["pem2048"].replace("\n", " ")  # one physical line: the key's newlines become spaces
start = 16_384 - 800
body = bytearray(b"x" * 20_000)
body[start : start + len(key)] = key.encode()
body = bytes(body) + b"\n"
os.makedirs(sys.argv[2], exist_ok=True)
open(os.path.join(sys.argv[2], "line.txt"), "wb").write(body)
json.dump({"key_start": start, "key_end": start + len(key), "line_bytes": len(body)}, open(os.path.join(sys.argv[2], "plan.json"), "w"))
print("line", len(body), "bytes; key", start, "to", start + len(key))
