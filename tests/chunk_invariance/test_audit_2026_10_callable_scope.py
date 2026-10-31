"""Audit 2026-10: the MCP check may run only the project's own functions.

Finding chunk-invariance/chunk_invariance/_worker.py:resolve:unscoped-callable-target: target,
flush_target and whole_target came straight from tool arguments and resolve() imported any
module, so ``whole_target="subprocess:getoutput"`` ran each sample as a shell command. Only
harmless ``echo`` commands are used here, and each test checks that the marker never appears.
"""

import json
import os
import shutil
import sys
import sysconfig
import textwrap
from pathlib import Path

import pytest
from chunk_invariance._check import CheckError, run_check

IDENTITY = """
import subprocess, os

def stream(chunks):
    for chunk in chunks:
        yield chunk

def process(chunk, state):
    return chunk

def flush(state):
    return ""

class Redactor:
    def __call__(self, text):
        return text

REDACTOR = Redactor()
"""


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "filters.py").write_text(textwrap.dedent(IDENTITY), encoding="utf-8")
    return project


def _echo_sample(marker: Path) -> str:
    return f"echo audit-dummy > {marker}; echo AUDIT_DUMMY_OUTPUT"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target": "filters:stream", "whole_target": "subprocess:getoutput"},
        {"target": "builtins:list", "whole_target": "subprocess:getoutput"},
        # Reaching a stdlib callable through the project module's own imports.
        {"target": "filters:stream", "whole_target": "filters:subprocess.getoutput"},
        {"target": "filters:subprocess.getoutput"},
        {"target": "filters:os.system"},
        {"target": "filters:process", "shape": "per_chunk", "flush_target": "filters:os.system"},
        {"target": "filters:process", "shape": "per_chunk", "flush_target": "os:system"},
    ],
)
def test_a_callable_outside_the_project_is_refused_and_never_runs(tmp_path, kwargs):
    project = _project(tmp_path)
    marker = project / "MARKER"
    with pytest.raises(CheckError, match="project_dir|not a module from"):
        run_check(str(project), samples=[_echo_sample(marker)], **kwargs)
    assert not marker.exists()


def test_a_project_dir_inside_the_standard_library_is_refused(tmp_path):
    marker = tmp_path / "MARKER"
    stdlib = sysconfig.get_paths()["stdlib"]
    with pytest.raises(CheckError, match="interpreter's library"):
        run_check(stdlib, "subprocess:getoutput", samples=[_echo_sample(marker)])
    assert not marker.exists()


def test_a_project_dir_containing_the_standard_library_is_refused(tmp_path):
    marker = tmp_path / "MARKER"
    with pytest.raises(CheckError, match="standard library"):
        run_check(os.path.abspath(os.sep), "subprocess:getoutput", samples=[_echo_sample(marker)])
    assert not marker.exists()


def test_the_python_argument_cannot_name_an_arbitrary_program(tmp_path):
    project = _project(tmp_path)
    echo = shutil.which("echo") or "/bin/echo"
    with pytest.raises(CheckError, match="is not allowed"):
        run_check(str(project), "filters:stream", samples=["abc"], python=echo)


def test_the_server_interpreter_and_a_project_function_still_work(tmp_path):
    project = _project(tmp_path)
    reply = run_check(str(project), "filters:stream", samples=["abc"], python=sys.executable)
    assert all(r["passed"] for r in reply["results"])
    # A callable instance of a project class is project code too.
    reply = run_check(str(project), "filters:stream", whole_target="filters:REDACTOR", samples=["abc"])
    assert all(r["passed"] for r in reply["results"])


def test_the_worker_does_not_inherit_the_server_environment(tmp_path, monkeypatch):
    project = _project(tmp_path)
    (project / "envleak.py").write_text(
        textwrap.dedent(
            """
            import os

            def stream(chunks):
                for chunk in chunks:
                    yield chunk

            def whole(text):
                return os.environ.get("AUDIT_DUMMY_SECRET", "absent")
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AUDIT_DUMMY_SECRET", "dummy-value-not-real")
    reply = run_check(str(project), "envleak:stream", whole_target="envleak:whole", samples=["abc"])
    assert "dummy-value-not-real" not in json.dumps(reply)
