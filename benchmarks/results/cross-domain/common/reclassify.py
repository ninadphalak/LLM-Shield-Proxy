"""Re-derives the leak class of every stored row with the final rule and rewrites the summaries.

python reclassify.py <fixtures.json> <report.json>...

whole:    the value is in the output verbatim.
fragment: the output still carries a prefix or a suffix of the value of 4+ characters while the
          whole value is gone: the filter fired on one piece and forwarded the other.
none:     neither.
Stored outputs are already masked for 12+ character windows, so a `fragment` row here is one
whose surviving piece is shorter than 12 characters; longer pieces were classified at run time.
"""
import json
import sys

fixtures = json.load(open(sys.argv[1], encoding="utf-8"))


def classify(out, value, current):
    """Never downgrades a run-time class. Upgrades `none` to `fragment` when a prefix or suffix
    of 4+ characters of the value is still in the (masked) output."""
    if current != "none":
        return current
    for k in range(4, len(value)):
        if value[:k] in out or value[-k:] in out:
            return "fragment"
    return "none"


for path in sys.argv[2:]:
    rep = json.load(open(path, encoding="utf-8"))
    for case in rep["cases"]:
        value = fixtures[case["name"]]
        if "output" in case["whole"]:
            case["whole"]["leak"] = classify(case["whole"]["output"], value, case["whole"]["leak"])
        for row in case["rows"]:
            if "output" in row:
                row["leak"] = classify(row["output"], value, row["leak"])
        leaking = [r for r in case["rows"] if r["leak"] != "none"]
        case["leaking_splits"] = len(leaking)
        case["leak_kinds"] = {k: sum(1 for r in leaking if r["leak"] == k) for k in ("whole", "fragment")}
        case["first_leaking_cut_in_value"] = leaking[0]["cut_in_value"] if leaking else None
        print(f"{path.split('/')[-2]}/{path.split('/')[-1]} {case['name']}: whole={case['whole']['leak']} splits={case['splits_tried']} leaking={len(leaking)} {case['leak_kinds']}")
    json.dump(rep, open(path, "w", encoding="utf-8", newline="\n"), indent=1)
