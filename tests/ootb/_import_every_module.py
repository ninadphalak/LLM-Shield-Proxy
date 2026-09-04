"""Import every module in the installed llm_shield_proxy, and fail on any missing dep.

Run by tests/ootb/test_pypi_cli.py with the interpreter of a WHEEL-ONLY virtualenv, so
its answer is about the published distribution and not about the developer environment.

Walks the filesystem rather than pkgutil.walk_packages: walk_packages swallows the
ImportError raised while importing a subpackage and then silently skips that
subpackage's children. On 1.5.1 that turned 50 modules into 10 and hid the broken one.
"""

from __future__ import annotations

import importlib
import pathlib
import sys

import llm_shield_proxy

root = pathlib.Path(llm_shield_proxy.__path__[0])
modules = sorted(
    {
        "llm_shield_proxy."
        + path.relative_to(root).with_suffix("").as_posix().replace("/", ".").removesuffix(".__init__")
        for path in root.rglob("*.py")
    }
)

missing: list[str] = []
for name in modules:
    try:
        importlib.import_module(name)
    except ModuleNotFoundError as exc:
        missing.append(f"{name}: {exc}")

print(f"WALKED {len(modules)}")
if missing:
    print("UNDECLARED_DEPENDENCY")
    for item in missing:
        print(f"  {item}")
    sys.exit(1)
