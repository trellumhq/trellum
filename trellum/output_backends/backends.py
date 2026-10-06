"""Pluggable output backends for publishing reports.

The framework writes to the local filesystem. A project that needs its built
reports to end up somewhere else implements the ``OutputBackend`` protocol and
registers it during bootstrap:

    from trellum.output_backends.backends import set_output_backend

    class MyBackend:
        def publish(self, output_dir: str, slug: str) -> None:
            ...

    set_output_backend(MyBackend())

If nothing is registered, the backend is resolved from the environment instead
(see ``resolve_output_backend``). That is what lets a deployment run
``--production`` against a mounted volume without a project-level
``bootstrap.py``.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class OutputBackend(Protocol):
    """Interface for production output destinations."""

    def publish(self, output_dir: str, slug: str) -> None:
        """Publish generated report output to its destination."""
        ...


_backend: OutputBackend | None = None
_resolved: OutputBackend | None = None


def set_output_backend(backend: OutputBackend) -> None:
    """Register the production output backend."""
    global _backend
    _backend = backend


def resolve_output_backend() -> OutputBackend | None:
    """Pick a backend from the environment. Returns ``None`` if unconfigured.

    ``BI_STORAGE_BACKEND`` selects explicitly:

        local  → LocalBackend  (output/ is a durable volume; publish is a no-op)

    Returning ``None`` when nothing is configured is deliberate: it preserves
    the "``--production`` needs a real destination" guard in the runner, so a
    ``bootstrap.py`` that silently failed to load can't turn into reports that
    generate successfully and are never published.
    """
    global _resolved
    if _resolved is not None:
        return _resolved

    choice = os.environ.get("BI_STORAGE_BACKEND", "").strip().lower()

    if choice == "local":
        from trellum.output_backends.local import LocalBackend

        _resolved = LocalBackend()
    else:
        return None

    return _resolved


def get_output_backend() -> OutputBackend | None:
    """Get the registered backend, falling back to the environment."""
    if _backend is not None:
        return _backend
    return resolve_output_backend()
