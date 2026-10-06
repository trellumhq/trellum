"""The demo project, shipped with the framework.

A fresh install should not start from an empty directory. This package carries a
complete, self-contained example project -- working reports over a fictional
mobile-games publisher -- so that after ``pip install`` there is something real
to build, read and copy from.

Everything here is fabricated. See ``tools/make_fixtures.py``: the numbers come
from a multiplicative model, so the charts show recognisable structure rather
than noise.

Install it into a project with::

    python -m trellum.demo

The report sources and the data-source registry ship inside the wheel. The
SQLite warehouse does **not** -- it is generated on demand, which keeps the
package small and, more usefully, dates the data relative to the day you install
rather than the day the release was built.
"""
from __future__ import annotations

from pathlib import Path

#: Directory holding the packaged demo project (reports/, data-sources/).
DEMO_ROOT = Path(__file__).resolve().parent

__all__ = ["DEMO_ROOT"]
