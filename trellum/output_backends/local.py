"""Local filesystem output writer and output backend."""

from __future__ import annotations

import os


class LocalBackend:
    """Output backend for deployments where ``output/`` is already durable.

    When the output directory is a mounted volume that outlives the process,
    there is nothing to upload and nothing to restore.

    Every method is therefore a no-op. It exists so ``--production`` has a
    valid backend to resolve when no remote destination is configured, rather
    than special-casing ``None`` at each call site.
    """

    def publish(self, output_dir: str, slug: str) -> None:
        """No-op: the runner already wrote the files to their final location."""

    def list_reports(self) -> list[str]:
        """No remote inventory to enumerate — local output is the source of truth."""
        return []

    def pull(self, slug: str, local_dir: str) -> None:
        """No-op: nothing to restore, the volume kept it."""


def write_local(content: str, path: str) -> str:
    """Write content to a local file, creating directories as needed.

    Returns the absolute path of the written file.
    """
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)
    return abs_path
