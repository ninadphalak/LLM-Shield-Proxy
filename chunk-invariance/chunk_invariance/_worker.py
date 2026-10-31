"""Runs inside the subprocess that imports the user's filter. Not part of the public API.

Reads one JSON request on stdin and writes one JSON result on stdout. The user's code may
print, so stdout is pointed at stderr while it runs. Standard library only, Python 3.9+,
because it runs on whatever interpreter the user's project uses.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import json
import os
import site
import sys
import sysconfig
from typing import Any, Callable, Dict, List, Optional


class TargetError(Exception):
    """The target could not be imported or is not a callable of the expected shape."""


def _real(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives on Windows
        return False


def interpreter_dirs() -> List[str]:
    """The running interpreter's standard library and every site-packages it can see."""
    found: List[str] = []
    for base in ({}, {"base": sys.base_prefix, "platbase": sys.base_exec_prefix}):
        paths = sysconfig.get_paths(vars=base) if base else sysconfig.get_paths()
        found += [paths[key] for key in ("stdlib", "platstdlib", "purelib", "platlib") if paths.get(key)]
    try:
        found += list(site.getsitepackages())
    except AttributeError:  # old virtualenv's site.py has no getsitepackages
        pass
    user_site = getattr(site, "getusersitepackages", lambda: None)()
    if user_site:
        found.append(user_site)
    for entry in sys.path:
        if entry and (os.path.basename(entry.rstrip("/\\")) in ("site-packages", "dist-packages") or entry.endswith(".zip")):
            found.append(entry)
    return sorted({_real(path) for path in found})


def _stdlib_dirs() -> List[str]:
    paths = sysconfig.get_paths()
    return sorted({_real(paths[key]) for key in ("stdlib", "platstdlib") if paths.get(key)})


def check_project_root(project_dir: str) -> str:
    """Refuse a project root that would make the interpreter's own library look like project code."""
    root = _real(project_dir)
    for directory in interpreter_dirs():
        if _inside(root, directory):
            raise TargetError(f"project_dir {project_dir!r} is inside the interpreter's library ({directory}); give the user's project")
    for directory in _stdlib_dirs():
        if _inside(directory, root):
            raise TargetError(f"project_dir {project_dir!r} contains the interpreter's standard library; give the user's project")
    return root


def _is_project_dir(path: str, roots: List[str], excluded: List[str]) -> bool:
    real = _real(path)
    return any(_inside(real, root) for root in roots) and not any(_inside(real, d) for d in excluded)


def _is_project_file(file: Optional[str], roots: List[str], excluded: List[str]) -> bool:
    return bool(file) and os.path.isfile(file) and _is_project_dir(file, roots, excluded)


def _defining_file(obj: Any) -> Optional[str]:
    """The source file of a Python function, method, class or callable instance; None for C code."""
    if inspect.ismethod(obj):
        obj = obj.__func__
    if inspect.isfunction(obj):
        return obj.__code__.co_filename
    if inspect.isbuiltin(obj) or inspect.ismethoddescriptor(obj) or type(obj).__name__ == "method-wrapper":
        return None
    cls = obj if inspect.isclass(obj) else type(obj)
    module = sys.modules.get(getattr(cls, "__module__", "") or "")
    return getattr(module, "__file__", None)


def _check_module_origin(module_name: str, roots: List[str], excluded: List[str]) -> None:
    # Checked part by part before anything is imported, so a module outside the project (a
    # standard-library module with import-time effects, say) is never even imported.
    parts = module_name.split(".")
    for i in range(1, len(parts) + 1):
        name = ".".join(parts[:i])
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            spec = None
        if spec is None:
            return  # import_module reports the missing module with its own message
        if spec.origin in (None, "namespace"):  # a namespace package: every folder must be the project's
            ok = bool(spec.submodule_search_locations) and all(
                os.path.isdir(loc) and _is_project_dir(loc, roots, excluded) for loc in spec.submodule_search_locations
            )
        else:  # "built-in" and "frozen" are not files, so they fail here
            ok = _is_project_file(spec.origin, roots, excluded)
        if not ok:
            raise TargetError(
                f"{name!r} is not a module from project_dir; only the project's own code can be checked, "
                "not the standard library or installed packages"
            )


def resolve(target: str, project_dir: str) -> Callable[..., Any]:
    """Import ``module:attr`` and return it, only if both the module and the final object are
    defined in a source file under project_dir and outside the interpreter's library."""
    module_name, sep, attr_path = target.partition(":")
    if not sep or not module_name or not attr_path:
        raise TargetError(f"target {target!r} must look like package.module:function")
    roots = [check_project_root(project_dir)]
    excluded = interpreter_dirs()
    _check_module_origin(module_name, roots, excluded)
    try:
        obj: Any = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise TargetError(
            f"cannot import {module_name!r} from {project_dir}: {exc}. The module path is relative to project_dir "
            "(or its src/ folder), the same as an import statement in your tests."
        ) from None
    except Exception as exc:
        raise TargetError(f"importing {module_name!r} raised {type(exc).__name__}: {exc}") from None
    if not _is_project_file(getattr(obj, "__file__", None), roots, excluded):
        raise TargetError(f"{module_name!r} is not a module from project_dir; only the project's own code can be checked")
    for part in attr_path.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError:
            raise TargetError(f"{module_name!r} has no attribute {attr_path!r}") from None
    if not callable(obj):
        raise TargetError(f"{target!r} is a {type(obj).__name__}, not a function")
    # The attribute path can reach anything the module imported (mymod:subprocess.getoutput), so
    # the object itself, not just its module, must be defined in a project source file.
    if not _is_project_file(_defining_file(obj), roots, excluded):
        raise TargetError(
            f"{target!r} is not defined in a source file under project_dir; only the project's own Python "
            "functions can be checked, not built-ins, the standard library or installed packages"
        )
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
    check_project_root(project_dir)
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
            entry.update(passed=False, raised=f"{type(exc).__name__}: {exc}")
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
    except SystemExit as exc:
        # The code may be a message, and a message can hold anything, so only an int is repeated.
        code = exc.code if isinstance(exc.code, int) else "with a message"
        reply = {
            "ok": False,
            "error": f"the target called sys.exit ({code}) while it was imported or run. What it printed is "
            "not shown, because it can hold anything your code wrote. Import the module yourself to see it.",
        }
    except BaseException as exc:
        reply = {"ok": False, "error": f"the check stopped on {type(exc).__name__}"}
    finally:
        sys.stdout = real_stdout
    real_stdout.write(json.dumps(reply))
    real_stdout.flush()


if __name__ == "__main__":
    main()
