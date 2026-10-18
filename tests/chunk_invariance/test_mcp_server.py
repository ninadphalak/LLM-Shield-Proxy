"""The optional MCP server: three tools that run the every-split check on a user's own filter.

The runner and the generated test need only the standard library, so those tests run everywhere.
The tests that talk to the server need the ``mcp`` extra (MCP SDK 2.x) and skip without it.
"""

import asyncio
import importlib.util
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from chunk_invariance._check import CheckError, format_report, render_test, run_check

LIBRARY_DIST = Path(__file__).resolve().parents[2] / "chunk-invariance"
EXAMPLES = "chunk_invariance.examples"
KEY_SAMPLES = ["use sk-AbCd1234 now", "Use key sk-FAKEab12cd34ef56gh78 for the demo."]


def _has_mcp2() -> bool:
    try:
        return importlib.util.find_spec("mcp.server.mcpserver") is not None
    except ImportError:
        return False


needs_mcp = pytest.mark.skipif(not _has_mcp2(), reason='needs the mcp extra: pip install "chunk-invariance[mcp]"')


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(LIBRARY_DIST), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    return env


def _project(tmp_path: Path, name: str, source: str) -> Path:
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return tmp_path


# The runner, without the MCP SDK.


def test_the_leaking_designs_fail_and_the_hold_back_filter_passes():
    for design in ("design_a", "design_b"):
        reply = run_check(str(LIBRARY_DIST), f"{EXAMPLES}:{design}", samples=KEY_SAMPLES)
        assert not any(r["passed"] for r in reply["results"]), design
    reply = run_check(str(LIBRARY_DIST), f"{EXAMPLES}:hold_back", samples=KEY_SAMPLES)
    assert all(r["passed"] for r in reply["results"])
    assert sum(r["splits_checked"] for r in reply["results"]) == 19 + 45


def test_the_report_names_the_split_and_the_value_that_leaked():
    report = format_report(run_check(str(LIBRARY_DIST), f"{EXAMPLES}:design_a", samples=KEY_SAMPLES[:1]))
    assert report.startswith("FAIL: chunk_invariance.examples:design_a is not chunk-invariant on 1 of 1 samples.")
    assert "First failing split: 'use s' + 'k-AbCd1234 now'" in report
    assert "'sk-AbCd1234' came out unredacted." in report
    assert "hold back the rest: at least (longest value length - 1) characters" in report


def test_the_report_says_when_a_sample_tests_nothing():
    report = format_report(run_check(str(LIBRARY_DIST), f"{EXAMPLES}:hold_back"))
    assert report.startswith("PASS:")
    assert "Samples 1, 2, 3, 5, 6 were not changed by the whole-text filter" in report


def test_a_value_that_leaks_only_in_part_is_named(tmp_path):
    project = _project(
        tmp_path,
        "emails",
        r"""
        import re

        def redact(text):
            return re.sub(r"[\w.]+@[\w-]+\.[a-z]{2,}", "[EMAIL]", text)

        def stream(chunks):
            for chunk in chunks:
                yield redact(chunk)
        """,
    )
    reply = run_check(str(project), "emails:stream", whole_target="emails:redact", samples=["Contact jane.doe@example.com now."])
    report = format_report(reply)
    # The rest of the address is still an address, so only the first letter leaks.
    assert "First failing split: 'Contact j' + 'ane.doe@example.com now.'" in report
    assert "The stream sent 'j', which the whole-text output does not have." in report
    assert "The start of 'jane.doe@example.com' went out before the filter saw the whole value." in report


def test_a_missing_flush_is_named(tmp_path):
    project = _project(
        tmp_path,
        "noflush",
        """
        def process(chunk, state):
            buffer = state.get("buffer", "") + chunk
            state["buffer"] = buffer[-3:]
            return buffer[:-3]

        def flush(state):
            return state.get("buffer", "")

        def unchanged(text):
            return text
        """,
    )
    check = dict(samples=["hello world"], whole_target="noflush:unchanged")
    report = format_report(run_check(str(project), "noflush:process", "per_chunk", **check))
    assert "stops early: text held back is never flushed" in report
    assert run_check(str(project), "noflush:process", "per_chunk", flush_target="noflush:flush", **check)["results"][0]["passed"]
    # Without whole_target the comparison is the same filter on one chunk, which drops the same tail.
    default = format_report(run_check(str(project), "noflush:process", "per_chunk", samples=["hello world"]))
    assert default.startswith("PASS:") and "pass whole_target to also catch a missing end-of-stream flush" in default


def test_an_async_generator_filter_is_checked(tmp_path):
    project = _project(
        tmp_path,
        "asyncfilter",
        """
        async def redact(chunks):
            async for chunk in chunks:
                yield chunk.replace("secret", "[X]")
        """,
    )
    reply = run_check(str(project), "asyncfilter:redact", "async_generator", samples=["a secret here"])
    assert not reply["results"][0]["passed"]
    assert reply["results"][0]["chunks"] == ["a s", "ecret here"]


def test_a_filter_that_hangs_is_stopped_at_the_timeout(tmp_path):
    project = _project(
        tmp_path,
        "hang",
        """
        import time

        def stream(chunks):
            while True:
                time.sleep(0.05)
            yield ""
        """,
    )
    with pytest.raises(CheckError, match="did not finish within 3 seconds and was stopped"):
        run_check(str(project), "hang:stream", samples=["x y"], timeout_seconds=3)


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("chunk_invariance.examples", "must look like package.module:function"),
        ("nope.mod:f", "cannot import 'nope.mod'"),
        (f"{EXAMPLES}:no_such", "has no attribute 'no_such'"),
        (f"{EXAMPLES}:KEY_LENGTH", "is a int, not a function"),
    ],
)
def test_a_bad_target_gives_a_clear_error(target, message):
    with pytest.raises(CheckError, match=re.escape(message)):
        run_check(str(LIBRARY_DIST), target)


def test_a_bad_project_dir_or_splits_value_is_refused_before_running():
    with pytest.raises(CheckError, match="is not a directory"):
        run_check(str(LIBRARY_DIST / "missing"), f"{EXAMPLES}:design_a")
    with pytest.raises(CheckError, match="int from 1 to 3"):
        run_check(str(LIBRARY_DIST), f"{EXAMPLES}:design_a", splits=4)


def test_what_the_filter_prints_does_not_break_the_result(tmp_path):
    project = _project(
        tmp_path,
        "noisy",
        """
        print("importing noisy")

        def stream(chunks):
            for chunk in chunks:
                print("chunk", chunk)
                yield chunk
        """,
    )
    assert all(r["passed"] for r in run_check(str(project), "noisy:stream", samples=["abc"])["results"])


def _run_generated(tmp_path: Path, source: str) -> subprocess.CompletedProcess:
    test_file = tmp_path / "test_generated.py"
    test_file.write_text(source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-q", "-p", "no:cacheprovider", "--rootdir", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_env(),
        timeout=120,
    )


def test_the_generated_test_passes_for_hold_back_and_fails_for_design_a(tmp_path):
    passing = _run_generated(tmp_path, render_test(f"{EXAMPLES}:hold_back", samples=KEY_SAMPLES))
    assert passing.returncode == 0, passing.stdout + passing.stderr
    assert "2 passed" in passing.stdout
    failing = _run_generated(tmp_path, render_test(f"{EXAMPLES}:design_a", samples=KEY_SAMPLES))
    assert failing.returncode == 1, failing.stdout + failing.stderr
    assert "2 failed" in failing.stdout
    assert "ChunkInvarianceError" in failing.stdout


def test_the_generated_test_for_each_shape_compiles():
    for shape, flush in (("generator", None), ("per_chunk", "pkg.mod:flush"), ("async_generator", None)):
        source = render_test("pkg.mod:Cls.method", shape, flush_target=flush, whole_target="pkg.mod:redact", splits=3)
        compile(source, f"<{shape}>", "exec")
        assert "splits=3" in source


def test_the_command_without_the_extra_says_how_to_install_it():
    script = "import sys; sys.modules['mcp'] = None\nfrom chunk_invariance.mcp_server import main\nmain()"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=_env(), timeout=60)
    assert result.returncode == 1
    if sys.version_info < (3, 10):
        assert "needs Python 3.10 or later" in result.stderr
    else:
        assert 'pip install "chunk-invariance[mcp]"' in result.stderr


# The server, through the MCP SDK.


def _call(*calls):
    from chunk_invariance.mcp_server import _build
    from mcp import Client

    async def go():
        async with Client(_build()) as client:
            return [await (client.list_tools() if name is None else client.call_tool(name, args)) for name, args in calls]

    return asyncio.run(go())


@needs_mcp
def test_the_server_lists_three_tools_and_says_it_runs_local_code():
    (tools,) = _call((None, None))
    by_name = {tool.name: tool for tool in tools.tools}
    assert sorted(by_name) == ["check_stream_filter", "explain", "write_regression_test"]
    for name in ("check_stream_filter", "write_regression_test"):
        assert "runs the user's own code from project_dir on this machine" in by_name[name].description
        assert by_name[name].input_schema["required"] == ["project_dir", "target"]


@needs_mcp
def test_check_stream_filter_through_the_server():
    args = {"project_dir": str(LIBRARY_DIST), "samples": KEY_SAMPLES}
    a, b, ok = _call(
        ("check_stream_filter", {**args, "target": f"{EXAMPLES}:design_a"}),
        ("check_stream_filter", {**args, "target": f"{EXAMPLES}:design_b"}),
        ("check_stream_filter", {**args, "target": f"{EXAMPLES}:hold_back"}),
    )
    assert not a.is_error and a.content[0].text.startswith("FAIL:")
    assert not b.is_error and b.content[0].text.startswith("FAIL:")
    assert not ok.is_error and ok.content[0].text.startswith("PASS:")
    # Plain text for the model to read, with no JSON wrapper around it.
    assert a.structured_content is None


@needs_mcp
def test_errors_come_back_as_tool_errors():
    bad, bad_splits = _call(
        ("check_stream_filter", {"project_dir": str(LIBRARY_DIST), "target": "nope.mod:f"}),
        ("check_stream_filter", {"project_dir": str(LIBRARY_DIST), "target": "x:f", "splits": 4}),
    )
    assert bad.is_error and "cannot import 'nope.mod'" in bad.content[0].text
    assert bad_splits.is_error and "splits" in bad_splits.content[0].text


@needs_mcp
def test_write_regression_test_returns_a_file_that_runs(tmp_path):
    (reply,) = _call(
        (
            "write_regression_test",
            {"project_dir": str(LIBRARY_DIST), "target": f"{EXAMPLES}:design_a", "samples": KEY_SAMPLES},
        )
    )
    text = reply.content[0].text
    assert text.startswith("Suggested file: tests/test_design_a_chunk_invariance.py\nIt fails today on 2 of 2 samples")
    source = text.split("```python\n", 1)[1].rsplit("```", 1)[0]
    result = _run_generated(tmp_path, source)
    assert "2 failed" in result.stdout, result.stdout + result.stderr


@needs_mcp
def test_explain_needs_no_input():
    (reply,) = _call(("explain", {}))
    assert "'use s' and 'k-AbCd1234 now'" in reply.content[0].text


@needs_mcp
def test_the_command_serves_over_stdio():
    from mcp import Client, StdioServerParameters

    params = StdioServerParameters(command=sys.executable, args=["-m", "chunk_invariance.mcp_server"], env=_env())

    async def go():
        async with Client(params) as client:
            tools = await client.list_tools()
            reply = await client.call_tool("explain", {})
            return sorted(t.name for t in tools.tools), reply.content[0].text

    names, text = asyncio.run(go())
    assert names == ["check_stream_filter", "explain", "write_regression_test"]
    assert text.startswith("A streaming filter sees text in chunks")
