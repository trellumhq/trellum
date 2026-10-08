"""Copy repository content without dereferencing repository-authored links."""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path


def reject_symlink(path: Path) -> None:
    if stat.S_ISLNK(path.lstat().st_mode) or getattr(path, "is_junction", lambda: False)():
        raise ValueError(f"Repository symlinks are not allowed in build inputs: {path}")


def safe_copyfile(source: Path, destination: Path, *, root: Path | None = None) -> None:
    source, destination = Path(os.path.abspath(source)), Path(destination)
    if root is not None:
        root = Path(os.path.abspath(root))
        _validate_ancestors(source, root)
        current = root
        for part in source.relative_to(root).parts[:-1]:
            current /= part
            reject_symlink(current)
        if not current.is_dir():
            raise ValueError(f"Build input parent is not a directory: {current}")
    reject_symlink(source)
    if not stat.S_ISREG(source.lstat().st_mode):
        raise ValueError(f"Build input is not a regular file: {source}")
    # O_NOFOLLOW closes the file-level check/open race on platforms that expose it.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(source, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"Build input is not a regular file: {source}")
        with os.fdopen(fd, "rb", closefd=False) as src, open(destination, "wb") as dst:
            shutil.copyfileobj(src, dst)
    finally:
        os.close(fd)


def safe_copytree(source: Path, destination: Path, *, root: Path | None = None) -> None:
    source = Path(os.path.abspath(source))
    if root is not None:
        root = Path(os.path.abspath(root))
    reject_symlinks(source, root=root)
    shutil.copytree(
        source, destination, symlinks=True,
        copy_function=lambda src, dst: safe_copyfile(src, dst, root=source),
    )
    try:
        reject_symlinks(destination)
    except ValueError:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _validate_ancestors(path: Path, root: Path) -> None:
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(path))
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Build input is outside its repository root: {path}") from exc
    resolved_root = root.resolve()
    current = root
    reject_symlink(current)
    for part in relative.parts:
        current /= part
        reject_symlink(current)
        if not current.resolve().is_relative_to(resolved_root):
            raise ValueError(f"Build input resolves outside its repository root: {current}")


def reject_symlinks(path: Path, *, root: Path | None = None) -> None:
    """Reject links and special files before any report content is used."""
    path = Path(path)
    if root is not None:
        _validate_ancestors(path, root)
    else:
        reject_symlink(path)
    if path.is_dir():
        for base, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                path = Path(base, name)
                mode = path.lstat().st_mode
                reject_symlink(path)
                if name in files and not stat.S_ISREG(mode):
                    raise ValueError(f"Build input is not a regular file: {path}")
