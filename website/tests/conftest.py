"""One precondition the whole suite rests on: the documentation corpus.

``content/docs/`` is generated, never committed — ``scripts/sync-docs.sh``
renders one directory per released tag of ``trellumhq/trellum`` (and, before
the first tag exists, its current HEAD as ``latest``). Without it the site
serves nothing under ``/docs/``.

That is worth failing loudly for, because the quiet half is much worse than
the loud half. Seven tests assert on docs pages and fail outright — but a
further **29 never exist at all**: ``test_site.py`` parametrizes over
``nav.pages("latest")``, so with an empty corpus those suites collect zero
cases and the run reports 76 passing tests instead of 105. A green-looking
run that has silently stopped checking every documentation page, every
internal link and every figure is the failure mode this guard exists to
prevent.

So: refuse to run at all, and say exactly which command fixes it. A missing
prerequisite is not a test failure and should not read like one.
"""

from pathlib import Path

import pytest

#: website/content/docs — resolved from this file rather than from Django
#: settings, because this hook runs before pytest-django has configured them.
DOCS_ROOT = Path(__file__).resolve().parent.parent / "content" / "docs"


def pytest_configure(config):
    if any(p.is_dir() for p in DOCS_ROOT.glob("*")):
        return

    raise pytest.UsageError(
        f"No documentation versions under {DOCS_ROOT}.\n"
        "\n"
        "content/docs/ is generated, not committed. Render it first:\n"
        "\n"
        "    ./scripts/sync-docs.sh\n"
        "\n"
        "Running without it does not just fail the docs tests -- it silently\n"
        "drops 29 of them from collection, because they are parametrized over\n"
        "the corpus. Refusing to start is the honest answer."
    )
