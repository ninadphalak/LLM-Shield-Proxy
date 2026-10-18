"""An MCP server so an AI coding assistant can run the every-split check on a user's own filter.

Needs the ``mcp`` extra (``pip install "chunk-invariance[mcp]"``); this is the only module that
imports the MCP SDK. ``chunk-invariance-mcp`` starts it on stdio. It runs on the user's machine:
checking a filter imports and runs the user's own code there, in a subprocess with a timeout.
The server makes no network calls and writes no files, but its results go back to the assistant,
which sends them to its model provider: the samples must be synthetic.
"""

import sys
from typing import Annotated, Literal, Optional, Union

from . import __version__
from ._check import (
    DEFAULT_SAMPLES,
    DEFAULT_TIMEOUT_SECONDS,
    EXPLANATION,
    MAX_TIMEOUT_SECONDS,
    CheckError,
    format_report,
    regression_test_name,
    render_test,
    run_check,
)

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from pydantic import Field
except ImportError as exc:  # pragma: no cover - exercised by main() without the extra
    _MISSING = exc
else:
    _MISSING = None

_SAFETY = (
    "It imports and runs the user's own code from project_dir on this machine, in a separate "
    "process that is stopped after timeout_seconds. The server makes no network calls and writes no "
    "files. Its result repeats the samples, any value the filter leaked and the message of any exception "
    "the user's code raised, and that result goes into this conversation, so use synthetic samples only, "
    "never a real secret or real personal data. What the user's code prints is never returned."
)

INSTRUCTIONS = (
    "Tests whether a streaming filter (a redactor, guardrail or rewriter that processes text in "
    "chunks) gives the same output however its input is split. A filter that scans each chunk alone "
    "leaks a secret or personal value cut across two chunks. Use check_stream_filter on the user's "
    "function, then write_regression_test to add a pytest file to their repo so CI keeps checking it. "
    "explain gives the background. " + _SAFETY
)


def _build() -> "MCPServer":
    server = MCPServer(name="chunk-invariance", version=__version__, instructions=INSTRUCTIONS)

    ProjectDir = Annotated[
        str,
        Field(description="Absolute path to the user's project root. The target is imported from here (and from its src/ folder)."),
    ]
    Target = Annotated[
        str,
        Field(
            description="The streaming filter as package.module:function, importable from project_dir the way the user's tests import it.",
        ),
    ]
    Shape = Annotated[
        Literal["generator", "per_chunk", "async_generator"],
        Field(
            description=(
                "generator: target(chunks) takes an iterable of text chunks and yields or returns output chunks. "
                "per_chunk: target(chunk, state) returns the output for one chunk; state is a dict kept across "
                "the stream; give the end-of-stream step as flush_target. async_generator: target takes an async "
                "iterable of chunks and yields output."
            )
        ),
    ]
    FlushTarget = Annotated[
        Optional[str],
        Field(description="per_chunk only: package.module:function for flush(state), called once after the last chunk."),
    ]
    WholeTarget = Annotated[
        Optional[str],
        Field(
            description=(
                "The non-streaming function, package.module:function, taking the whole text and returning the "
                "filtered text. Default: the stream filter run on the whole text as one chunk, which cannot catch a "
                "missing end-of-stream flush (one chunk drops the same tail), so give it when one exists."
            )
        ),
    ]
    Samples = Annotated[
        Optional[list[str]],
        Field(
            description=(
                "Input texts that contain values the filter should redact or rewrite. Default: synthetic "
                f"samples (an email, an SSN, a card number, API-key shapes; all fake): {list(DEFAULT_SAMPLES)!r}. "
                "Pass made-up values in the formats this filter matches (for example a key typed in the "
                "provider's format), or the check proves nothing. Never real secrets or personal data: "
                "samples are echoed in the result and in the generated test file."
            )
        ),
    ]
    Splits = Annotated[
        Union[Literal["all-two-part"], Annotated[int, Field(ge=1, le=3)]],
        Field(
            description="'all-two-part' (default): the whole text plus every single cut. An int up to 3: every split into at most that many parts (slower)."
        ),
    ]
    Python = Annotated[
        Optional[str],
        Field(
            description=(
                "Interpreter that imports the user's code. Default: project_dir/.venv or project_dir/venv "
                "if present, else the server's own Python."
            )
        ),
    ]
    Timeout = Annotated[
        float,
        Field(gt=0, le=MAX_TIMEOUT_SECONDS, description="Seconds before the check process is stopped."),
    ]

    @server.tool(
        structured_output=False,
        description=(
            "Check whether the user's streaming filter leaks or changes values that are split across chunks. "
            "Streams each sample through the filter in every two-part split (or more parts) and compares the "
            "joined output with filtering the whole text at once. Returns PASS or FAIL, the first failing split "
            "in plain words (which value came out unredacted), the number of splits checked, and how to fix it. "
            + _SAFETY
        )
    )
    def check_stream_filter(
        project_dir: ProjectDir,
        target: Target,
        shape: Shape = "generator",
        flush_target: FlushTarget = None,
        whole_target: WholeTarget = None,
        samples: Samples = None,
        splits: Splits = "all-two-part",
        python: Python = None,
        timeout_seconds: Timeout = DEFAULT_TIMEOUT_SECONDS,
    ) -> str:
        try:
            reply = run_check(
                project_dir,
                target,
                shape,
                flush_target=flush_target,
                whole_target=whole_target,
                samples=samples,
                splits=splits,
                python=python,
                timeout_seconds=timeout_seconds,
            )
        except CheckError as exc:
            raise ToolError(str(exc)) from None
        return format_report(reply)

    @server.tool(
        structured_output=False,
        description=(
            "Write a pytest file that runs chunk_invariance.assert_chunk_invariant on the user's streaming filter "
            "for each sample, so their CI keeps checking that values split across chunks cannot leak. Takes the "
            "same inputs as check_stream_filter, first checks that the target imports, and returns the file text "
            "with a suggested file name and whether it passes today. Save it in the user's tests folder; the test "
            "environment needs `pip install chunk-invariance`. " + _SAFETY
        )
    )
    def write_regression_test(
        project_dir: ProjectDir,
        target: Target,
        shape: Shape = "generator",
        flush_target: FlushTarget = None,
        whole_target: WholeTarget = None,
        samples: Samples = None,
        splits: Splits = "all-two-part",
        python: Python = None,
        timeout_seconds: Timeout = DEFAULT_TIMEOUT_SECONDS,
    ) -> str:
        try:
            reply = run_check(
                project_dir,
                target,
                shape,
                flush_target=flush_target,
                whole_target=whole_target,
                samples=samples,
                splits=splits,
                python=python,
                timeout_seconds=timeout_seconds,
            )
        except CheckError as exc:
            raise ToolError(str(exc)) from None
        failing = sum(1 for result in reply["results"] if not result["passed"])
        status = (
            f"It fails today on {failing} of {len(reply['results'])} samples; it passes once the filter is fixed."
            if failing
            else "It passes today."
        )
        source = render_test(
            target, shape, flush_target=flush_target, whole_target=whole_target, samples=samples, splits=splits
        )
        return f"Suggested file: tests/{regression_test_name(target)}\n{status}\n\n```python\n{source}```\n"

    @server.tool(
        structured_output=False,
        description="Explain in one paragraph why filtering a stream chunk by chunk leaks values cut across chunks, with an example and the fix. No inputs.",
    )
    def explain() -> str:
        return EXPLANATION

    return server


def main() -> None:
    """Start the server on stdio (the ``chunk-invariance-mcp`` command)."""
    if sys.version_info < (3, 10):
        sys.exit("chunk-invariance-mcp needs Python 3.10 or later (the MCP SDK does); the library itself runs on 3.9")
    if _MISSING is not None:
        sys.exit(f'chunk-invariance-mcp needs the MCP SDK 2.x: pip install "chunk-invariance[mcp]" ({_MISSING})')
    _build().run("stdio")


if __name__ == "__main__":
    main()
