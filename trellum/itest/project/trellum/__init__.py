"""Dev-only shim so this repo is importable as ``trellum`` from ``itest/project/``.

Copy of ``demo/trellum/__init__.py`` with one extra ``os.pardir`` hop: the
demo shim lives two directories below the repo root (``demo/trellum/``),
this one lives three (``itest/project/trellum/``), so it needs one more
step up to land on the same repo root.

Consumer projects mount this repo as a git submodule named literally
``trellum/``, so ``import trellum`` resolves naturally there. Standalone,
the repo root is not on ``sys.path``, the import fails, and nothing in
the repo can be run or tested.

This package fixes that without duplicating code: it repoints its own
submodule search path at the repo root, then executes the real top-level
``__init__.py``. Every ``trellum.*`` submodule therefore resolves to the
actual repo modules.

Why a shim and not a symlink or an editable install:

* It survives subprocesses. ``itest/test_report_build.py`` spawns
  ``python -m trellum.run`` with ``cwd`` set to ``itest/project``, and the
  child resolves ``trellum`` through this same shim -- no env var, no
  ``PYTHONPATH``, no install step.
* A junction/symlink pointing at the repo root would contain ``itest/``
  itself and recurse forever for any tree walker.
* An editable install would need a hand-maintained package list that rots
  whenever a subpackage is added.

This file is dev-only cargo. It is never copied into a consumer project, and
nothing in the framework imports it.
"""

import os

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, os.pardir)
)

# Redirect submodule lookup (trellum.runner, trellum.components, ...) at
# the repo root before executing the real __init__, whose absolute imports
# depend on this path already being in place.
__path__ = [_REPO_ROOT]

_real_init = os.path.join(_REPO_ROOT, "__init__.py")
with open(_real_init, encoding="utf-8") as _fh:
    exec(compile(_fh.read(), _real_init, "exec"))
