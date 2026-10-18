"""What the MCP tools do, without the MCP SDK: run the check in a subprocess, describe the
result in plain words, and write the regression test. Not part of the public API.

Standard library only, so the core package keeps no dependency; ``mcp_server`` wraps these.
It imports subprocess on purpose: the check runs the user's own filter on their own machine.
"""

from __future__ import annotations

import difflib
import json
import os
import subprocess  # nosec B404
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

SHAPES = ("generator", "per_chunk", "async_generator")
DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 600

# Every value is synthetic. example.com is reserved for documentation (RFC 2606), 987-65-4320 to
# 987-65-4329 are the Social Security numbers the SSA reserves for advertising, 4111 1111 1111 1111
# is the Visa test card, AKIAIOSFODNN7EXAMPLE is the example key in AWS's own docs, and the ghp_ token
# says it is not real.
DEFAULT_SAMPLES = (
    "Contact jane.doe@example.com about the invoice.",
    "SSN on file: 987-65-4321 (fake).",
    "Card 4111 1111 1111 1111 exp 12/30 (test card).",
    "Use key sk-FAKEab12cd34ef56gh78 for the demo.",
    "aws_access_key_id = AKIAIOSFODNN7EXAMPLE",
    "token ghp_EXAMPLENOTAREALGITHUBTOKEN0000000000 ok",
)

FIX_HINT = (
    "Fix: keep a buffer. Append each chunk to it, scan the joined text, emit only the part that "
    "cannot be the start of a value, and hold back the rest: at least (longest value length - 1) "
    "characters. At end of stream, scan and emit what is left (flush). A filter that scans each "
    "chunk alone, or that prepends a fixed tail of the previous chunk after already emitting it, "
    "leaks a value cut across two chunks."
)

EXPLANATION = (
    "A streaming filter sees text in chunks, and the chunk boundaries fall wherever the model, the "
    "network or the parser put them, not where your values are. If the filter scans each chunk on "
    "its own, a value cut in two matches in neither half: with a key pattern sk- plus eight "
    "characters, the input 'use sk-AbCd1234 now' arrives as 'use s' and 'k-AbCd1234 now', neither "
    "chunk holds the whole key, and 'use sk-AbCd1234 now' goes out unredacted although the same "
    "filter on the whole text gives 'use [KEY] now'. Prepending a fixed tail of the previous chunk "
    "does not fix it, because that tail was already emitted before the key was recognised. The "
    "correct rule: for every way of splitting the input into chunks, the joined streamed output must "
    "equal the output of filtering the whole input at once. The fix is to hold back at least the "
    "longest value length minus one characters, scan the joined text, and flush at end of stream. "
    "check_stream_filter tests that rule on your function over every two-part split of each sample, "
    "and write_regression_test turns it into a pytest file for your CI. Background: Phalak, N. "
    "(2026), Split-Boundary Leaks in Streaming Guardrails, doi:10.5281/zenodo.22909585."
)

Splits = Union[str, int]


class CheckError(Exception):
    """The check could not run: bad input, a target that does not import, or a timeout."""


def _package_dir() -> str:
    return str(Path(__file__).resolve().parent)


# Loads this copy of chunk_invariance into the user's interpreter by file path, without putting
# the server's site-packages on its sys.path, then runs the worker. The working directory is the
# project, so its modules import as they do in its own tests.
_BOOTSTRAP = (
    "import importlib.util, sys\n"
    "pkg = sys.argv[1]\n"
    "spec = importlib.util.spec_from_file_location('chunk_invariance', pkg + '/__init__.py',"
    " submodule_search_locations=[pkg])\n"
    "mod = importlib.util.module_from_spec(spec)\n"
    "sys.modules['chunk_invariance'] = mod\n"
    "spec.loader.exec_module(mod)\n"
    "from chunk_invariance._worker import main\n"
    "main()\n"
)


def find_python(project_dir: str, python: Optional[str] = None) -> str:
    """The interpreter that imports the user's code: the one given, the project's virtualenv, or ours."""
    if python:
        if not Path(python).is_file():
            raise CheckError(f"python {python!r} is not a file; give the path to an interpreter")
        return python
    for venv in (".venv", "venv"):
        for relative in ("Scripts/python.exe", "bin/python"):
            candidate = Path(project_dir) / venv / relative
            if candidate.is_file():
                return str(candidate)
    return sys.executable


def validate(
    project_dir: str,
    target: str,
    shape: str,
    splits: Splits,
    samples: Optional[Sequence[str]],
    timeout_seconds: float,
) -> List[str]:
    if not Path(project_dir).is_dir():
        raise CheckError(f"project_dir {project_dir!r} is not a directory")
    if ":" not in target:
        raise CheckError(f"target {target!r} must look like package.module:function")
    if shape not in SHAPES:
        raise CheckError(f"shape {shape!r} is not one of {', '.join(SHAPES)}")
    if splits != "all-two-part" and not (isinstance(splits, int) and not isinstance(splits, bool) and 1 <= splits <= 3):
        raise CheckError(f"splits {splits!r} must be 'all-two-part' or an int from 1 to 3")
    if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise CheckError(f"timeout_seconds must be above 0 and at most {MAX_TIMEOUT_SECONDS}")
    chosen = list(DEFAULT_SAMPLES if not samples else samples)
    if not all(isinstance(s, str) and s for s in chosen):
        raise CheckError("samples must be non-empty strings")
    return chosen


def run_check(
    project_dir: str,
    target: str,
    shape: str = "generator",
    *,
    flush_target: Optional[str] = None,
    whole_target: Optional[str] = None,
    samples: Optional[Sequence[str]] = None,
    splits: Splits = "all-two-part",
    python: Optional[str] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    resolve_only: bool = False,
) -> Dict[str, Any]:
    """Import the target in a subprocess and run the every-split check on each sample."""
    project_dir = str(Path(project_dir).resolve()) if project_dir else ""
    chosen = validate(project_dir, target, shape, splits, samples, timeout_seconds)
    if flush_target and shape != "per_chunk":
        raise CheckError("flush_target is only used with shape per_chunk")
    interpreter = find_python(project_dir, python)
    request = {
        "project_dir": project_dir,
        "target": target,
        "shape": shape,
        "flush_target": flush_target,
        "whole_target": whole_target,
        "samples": chosen,
        "splits": splits,
        "resolve_only": resolve_only,
    }
    started = time.monotonic()
    try:
        # An argv list with no shell; the interpreter is the user's own.
        completed = subprocess.run(  # nosec B603
            [interpreter, "-c", _BOOTSTRAP, _package_dir()],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=project_dir,
            timeout=timeout_seconds,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        raise CheckError(
            f"{target} did not finish within {timeout_seconds:g} seconds and was stopped. A filter that "
            "waits for more input, reads from a network, or loops forever hangs here. Raise "
            "timeout_seconds if the filter is just slow."
        ) from None
    except OSError as exc:
        raise CheckError(f"could not start {interpreter}: {exc}") from None
    try:
        reply = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        # Its output is not repeated: it can hold anything the user's code printed, and this
        # message goes back to the assistant.
        raise CheckError(
            f"the check process exited with code {completed.returncode} before it reported a result (for "
            "example os._exit, or a crash in native code). Its output is not shown, because it can hold "
            "anything your code printed. Import the module yourself in project_dir to see it."
        ) from None
    if not reply.get("ok"):
        raise CheckError(reply.get("error", "unknown error"))
    reply.update(
        python=interpreter, seconds=time.monotonic() - started, target=target, splits=splits, whole_target=whole_target
    )
    return reply


def _differences(a: str, b: str, bridge: int = 3) -> List[Tuple[str, str]]:
    """(part of a, part of b) for each region where they differ.

    Changes separated by fewer than ``bridge`` equal characters are one region, so 'sk-FAKEab12'
    replaced by '[KEY]' is one change, not a few letters that happen to line up.
    """
    regions: List[List[int]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if regions and i1 - regions[-1][1] < bridge and j1 - regions[-1][3] < bridge:
            regions[-1][1], regions[-1][3] = i2, j2
        else:
            regions.append([i1, i2, j1, j2])
    return [(a[i1:i2], b[j1:j2]) for i1, i2, j1, j2 in regions]


def describe_failure(entry: Dict[str, Any]) -> List[str]:
    if "raised" in entry:
        return [
            f"  The filter raised {entry['raised']}",
            "  Check that shape matches the function: generator takes an iterable of chunks and yields "
            "output; per_chunk is process(chunk, state) returning output; async_generator takes an "
            "async iterable.",
        ]
    chunks, expected, actual = entry["chunks"], entry["expected"], entry["actual"]
    lines = [f"  First failing split: {' + '.join(repr(c) for c in chunks)}"]
    values = [original for original, _ in _differences(entry["sample"], expected) if len(original) > 1]
    leaked = [value for value in values if value in actual]
    if leaked:
        lines.append(f"  {', '.join(repr(v) for v in leaked)} came out unredacted.")
    elif expected.startswith(actual):
        lines.append("  The streamed output stops early: text held back is never flushed at end of stream.")
    else:
        for want, got in _differences(expected, actual):
            if not want:
                note = f"  The stream sent {got!r}, which the whole-text output does not have."
            elif not got:
                note = f"  The stream dropped {want!r}, which the whole-text output has."
            else:
                note = f"  The stream sent {got!r} where filtering the whole text gives {want!r}."
            started = [v for v in values if got and got.strip() and v.startswith(got.strip())]
            if started:
                note += f" The start of {started[0]!r} went out before the filter saw the whole value."
            lines.append(note)
    lines.append(f"  whole:    {expected!r}")
    lines.append(f"  streamed: {actual!r}")
    return lines


def format_report(reply: Dict[str, Any]) -> str:
    results = reply["results"]
    failed = [i for i, r in enumerate(results, 1) if not r["passed"]]
    total = sum(r.get("splits_checked", 0) for r in results)
    splits = "every two-part split" if reply["splits"] == "all-two-part" else f"every split into at most {reply['splits']} parts"
    if failed:
        head = f"FAIL: {reply['target']} is not chunk-invariant on {len(failed)} of {len(results)} samples."
    else:
        head = f"PASS: {reply['target']} gave the whole-text output on every split of all {len(results)} samples."
    lines = [head, f"Checked {total} splits ({splits}) with {reply['python']} in {reply['seconds']:.1f} s."]
    if not reply.get("whole_target"):
        lines.append(
            "Compared with the stream filter run on the whole text as one chunk; pass whole_target to also catch "
            "a missing end-of-stream flush."
        )
    lines.append("")
    for i in failed:
        lines.append(f"Sample {i}: {results[i - 1]['sample']!r}")
        lines.extend(describe_failure(results[i - 1]))
        lines.append("")
    unchanged = [i for i, r in enumerate(results, 1) if r["passed"] and not r.get("changed")]
    if unchanged:
        lines.append(
            f"Samples {', '.join(map(str, unchanged))} were not changed by the whole-text filter, so they test "
            "nothing for this filter. Pass made-up samples in the formats it redacts (never real values)."
        )
        lines.append("")
    if failed:
        lines.append(FIX_HINT)
        lines.append("")
        lines.append("Call write_regression_test with the same inputs to get a pytest file that keeps this checked in CI.")
    return "\n".join(lines).rstrip() + "\n"


def _import_lines(target: str, alias: str) -> List[str]:
    module, _, attr_path = target.partition(":")
    first, _, rest = attr_path.partition(".")
    if rest:
        return [f"from {module} import {first}", f"{alias} = {first}.{rest}"]
    return [f"from {module} import {first} as {alias}"]


def render_test(
    target: str,
    shape: str = "generator",
    *,
    flush_target: Optional[str] = None,
    whole_target: Optional[str] = None,
    samples: Optional[Sequence[str]] = None,
    splits: Splits = "all-two-part",
) -> str:
    """A pytest file that runs ``assert_chunk_invariant`` on the target for each sample."""
    chosen = list(DEFAULT_SAMPLES if not samples else samples)
    adapters = {"per_chunk": "per_chunk", "async_generator": "from_async"}
    names = ["assert_chunk_invariant"] + ([adapters[shape]] if shape in adapters else [])
    imports = ["import pytest", f"from chunk_invariance import {', '.join(sorted(names))}", ""]
    setup: List[str] = []
    if shape == "per_chunk":
        imports += _import_lines(target, "process")
        if flush_target:
            imports += _import_lines(flush_target, "flush")
            setup.append("stream_filter = per_chunk(process, state=dict, flush=flush)")
        else:
            setup.append("stream_filter = per_chunk(process, state=dict)")
    elif shape == "async_generator":
        imports += _import_lines(target, "async_filter")
        setup.append("stream_filter = from_async(async_filter)")
    else:
        imports += _import_lines(target, "stream_filter")
    if whole_target:
        imports += _import_lines(whole_target, "whole_filter")
    head = [
        f'"""Chunk-invariance regression test for {target}.',
        "",
        "Streams each sample in every way of cutting it into chunks and checks that the joined output",
        "equals filtering the whole sample at once, so a value cut across two chunks cannot leak.",
        "Needs `pip install chunk-invariance` in the test environment. The samples are synthetic.",
        '"""',
        "",
        *imports,
    ]
    sections = [head]
    if setup:
        sections.append(setup)
    if not whole_target:
        sections.append(
            [
                "def whole_filter(text):",
                "    # The same filter given the whole input as one chunk.",
                '    return "".join(stream_filter(iter([text])))',
            ]
        )
    sections.append(["SAMPLES = [", *(f"    {s!r}," for s in chosen), "]"])
    sections.append(
        [
            '@pytest.mark.parametrize("sample", SAMPLES)',
            "def test_streamed_output_matches_whole_output(sample):",
            f"    assert_chunk_invariant(stream_filter, whole_filter, sample, splits={splits!r})",
        ]
    )
    return "\n\n\n".join("\n".join(section) for section in sections) + "\n"


def regression_test_name(target: str) -> str:
    stem = target.partition(":")[2].split(".")[-1] or "filter"
    return f"test_{stem}_chunk_invariance.py"
