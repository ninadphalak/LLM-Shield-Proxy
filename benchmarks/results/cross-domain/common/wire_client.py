"""Every-split client for filters that sit on the wire.

python wire_client.py <fixtures.json> <out.json> --target NAME --version V \
    (--reverse http://localhost:PORT | --proxy http://localhost:PORT --origin http://HOST:18080) \
    [--cases email,ssn,pem2048] [--margin 8] [--note TEXT]

--reverse: the filter is a reverse proxy in front of split_upstream; URLs go to it directly.
--proxy:   the filter is a forward proxy; URLs go to --origin through it.
For each case: whole text once, then every single cut from <margin> bytes before the value to
<margin> bytes after it. Output is classified as whole / fragment / none and stored masked.
"""
import argparse
import http.client
import json
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("fixtures")
ap.add_argument("out")
ap.add_argument("--target", required=True)
ap.add_argument("--version", required=True)
ap.add_argument("--reverse")
ap.add_argument("--proxy")
ap.add_argument("--origin", default="http://host.docker.internal:18080")
ap.add_argument("--cases", default="email,ssn,pem2048")
ap.add_argument("--margin", type=int, default=8)
ap.add_argument("--note", default="")
ap.add_argument("--cut-range", help="only cuts A..B counted into the value, e.g. 2040,2100")
args = ap.parse_args()

fixtures = json.load(open(args.fixtures, encoding="utf-8"))
if args.proxy:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": args.proxy}))
    base = args.origin
else:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = args.reverse


def fetch(path):
    # A filter that shortens the body without fixing Content-Length ends in IncompleteRead;
    # what arrived is still the transcript.
    # A Go server that wrote fewer bytes than it declared resets the connection instead.
    buf = b""
    with opener.open(base + path, timeout=30) as r:
        try:
            while True:
                piece = r.read(4096)
                if not piece:
                    break
                buf += piece
        except http.client.IncompleteRead as e:
            buf += e.partial
        except (ConnectionResetError, ConnectionAbortedError):
            pass
    return buf.decode("utf-8", "replace")


def classify(out, value):
    """whole: value forwarded verbatim. fragment: a prefix or suffix of 4+ characters survived
    (with two-part splits the pieces are always a prefix and a suffix). none: neither."""
    if value in out:
        return "whole"
    for k in range(4, len(value)):
        if value[:k] in out or value[-k:] in out:
            return "fragment"
    return "none"


def masked(out, value, window=12):
    out = out.replace(value, "#" * len(value))
    for i in range(len(value) - window + 1):
        piece = value[i : i + window]
        if piece in out:
            out = out.replace(piece, "#" * window)
    return out


cases_meta = json.loads(fetch("/cases"))
report = {
    "target": args.target,
    "version": args.version,
    "note": args.note,
    "splits": f"whole + every single cut from {args.margin} bytes before the value to {args.margin} after",
    "cases": [],
}
for name in args.cases.split(","):
    value = fixtures[name]
    start, length = cases_meta[name]["start"], cases_meta[name]["length"]
    whole = fetch(f"/whole/{name}")
    rows = []
    lo, hi = max(1, start - args.margin), start + length + args.margin
    if args.cut_range:
        a, b = (int(x) for x in args.cut_range.split(","))
        lo, hi = start + a, start + b
    for cut in range(lo, hi + 1):
        out = fetch(f"/split/{name}/{cut}")
        rows.append({"cut": cut, "cut_in_value": cut - start, "leak": classify(out, value), "output": masked(out, value)})
    leaking = [r for r in rows if r["leak"] != "none"]
    report["cases"].append(
        {
            "name": name,
            "value_length": length,
            "whole": {"leak": classify(whole, value), "output": masked(whole, value)},
            "splits_tried": len(rows),
            "leaking_splits": len(leaking),
            "first_leaking_cut_in_value": leaking[0]["cut_in_value"] if leaking else None,
            "rows": rows,
        }
    )
    print(f"{name}: whole={classify(whole, value)} splits={len(rows)} leaking={len(leaking)}", flush=True)

json.dump(report, open(args.out, "w", encoding="utf-8", newline="\n"), indent=1)
