"""Runs inside the subprocess that imports the user's filter. Not part of the public API.

Reads one JSON request on stdin and writes one JSON result on stdout. The user's code may
print, so stdout is pointed at stderr while it runs. Standard library only, Python 3.9+,
because it runs on whatever interpreter the user's project uses.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional


class TargetError(Exception):
    """The target could not be imported or is not a callable of the expected shape."""


def resolve(target: str, project_dir: str) -> Callable[..., Any]:
    module_name, sep, attr_path = target.partition(":")
    if not sep or not module_name or not attr_path:
        raise TargetError(f"target {target!r} must look like package.module:function")
    try:
        obj: Any = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise TargetError(
            f"cannot import {module_name!r} from {project_dir}: {exc}. The module path is relative to project_dir "
            "(or its src/ folder), the same as an import statement in your tests."
        ) from None
    except Exception as exc:
        raise TargetError(f"importing {module_name!r} raised {type(exc).__name__}: {exc}") from None
    for part in attr_path.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError:
            raise TargetError(f"{module_name!r} has no attribute {attr_path!r}") from None
    if not callable(obj):
        raise TargetError(f"{target!r} is a {type(obj).__name__}, not a function")
    return obj


def build_stream_filter(shape: str, target: Callable[..., Any], flush: Optional[Callable[..., Any]]):
    from chunk_invariance import from_async, per_chunk

    if shape == "generator":
        return target
    if shape == "per_chunk":
        return per_chunk(target, state=dict, flush=flush)
    if shape == "async_generator":
        return from_async(target)
    raise TargetError(f"unknown shape {shape!r}; use generator, per_chunk or async_generator")


def build_whole_filter(whole: Optional[Callable[..., Any]], stream_filter: Callable[..., Any]):
    if whole is None:

        def whole_filter(text: str) -> str:
            return "".join(stream_filter(iter([text])))

        return whole_filter

    def call_whole(text: str) -> Any:
        result = whole(text)
        if inspect.isawaitable(result):
            async def wait() -> Any:
                return await result

            result = asyncio.run(wait())
        return result

    return call_whole


def check(request: Dict[str, Any]) -> Dict[str, Any]:
    from chunk_invariance import ChunkInvarianceError, assert_chunk_invariant

    project_dir = request["project_dir"]
    for path in (os.path.join(project_dir, "src"), project_dir):
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)

    target = resolve(request["target"], project_dir)
    flush = resolve(request["flush_target"], project_dir) if request.get("flush_target") else None
    whole = resolve(request["whole_target"], project_dir) if request.get("whole_target") else None
    stream_filter = build_stream_filter(request["shape"], target, flush)
    whole_filter = build_whole_filter(whole, stream_filter)
    if request.get("resolve_only"):
        return {"ok": True}

    results: List[Dict[str, Any]] = []
    for sample in request["samples"]:
        entry: Dict[str, Any] = {"sample": sample}
        try:
            expected = whole_filter(sample)
            entry["changed"] = expected != sample
            entry["splits_checked"] = assert_chunk_invariant(stream_filter, whole_filter, sample, splits=request["splits"])
            entry["passed"] = True
        except ChunkInvarianceError as exc:
            entry.update(
                passed=False,
                chunks=[str(c) for c in exc.chunks],
                expected=str(exc.expected),
                actual=str(exc.actual),
                splits_checked=exc.splits_tried,
            )
        except Exception as exc:
            entry.update(
                passed=False,
                raised=f"{type(exc).__name__}: {exc}",
                traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-3:]),
            )
        results.append(entry)
    return {"ok": True, "results": results}


def main() -> None:
    request = json.loads(sys.stdin.read())
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        reply = check(request)
    except TargetError as exc:
        reply = {"ok": False, "error": str(exc)}
    except Exception as exc:
        reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        sys.stdout = real_stdout
    real_stdout.write(json.dumps(reply))
    real_stdout.flush()


if __name__ == "__main__":
    main()
