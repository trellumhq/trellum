"""Private local credential files; callers must use a trusted local directory."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tempfile


class CredentialPersistenceError(OSError):
    """A credential file could not safely be read or persisted."""


def _check(path: Path, *, directory=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or (not directory and info.st_nlink != 1)
            or (os.name == "posix" and info.st_uid != os.getuid())):
        raise CredentialPersistenceError("Credential path must be owned, regular and unlinked.")
    return info


def protect(path: Path, *, directory=False):
    if os.name == "nt":
        from trellum.private_files_windows import protect_windows
        protect_windows(path)
    else:
        os.chmod(path, 0o700 if directory else 0o600)


def prepare_parent(path: Path, *, cache=False):
    parent = path.parent
    if cache:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = _check(parent, directory=True)
    if info is None:
        raise CredentialPersistenceError("Credential directory does not exist.")
    if cache:
        protect(parent, directory=True)
    elif os.name == "posix" and info.st_mode & 0o022:
        raise CredentialPersistenceError("Credential directory must not be writable by other users.")


def read_private(path: Path) -> str:
    info = _check(path)
    if info is None:
        raise FileNotFoundError(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, encoding="utf-8", newline="") as handle:
        actual = os.fstat(handle.fileno())
        if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
            raise CredentialPersistenceError("Credential file changed while opening.")
        protect(path)
        return handle.read()


def atomic_write(path: Path, text: str):
    """Replace only after private staging, flush and fsync succeed."""
    _check(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            protect(temp)  # Windows DACL before writing any secret bytes.
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _check(path)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def private_lock(path: Path):
    """Persistent sibling lock: OS releases it on exit/crash; never unlink it."""
    import portalocker
    lock_path = path.with_name(path.name + ".lock")
    try:
        _check(lock_path)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "a+b") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (os.name == "posix" and info.st_uid != os.getuid())):
                raise CredentialPersistenceError("Unsafe credential lock file.")
            protect(lock_path)
            # Lock an already checked handle, with a bounded acquisition loop.
            import time
            deadline = time.monotonic() + 10
            while True:
                try:
                    portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
                    break
                except portalocker.exceptions.LockException:
                    if time.monotonic() >= deadline:
                        raise CredentialPersistenceError("Credential file is busy; no changes saved.") from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                portalocker.unlock(handle)
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, CredentialPersistenceError):
            raise
        raise CredentialPersistenceError("Credential persistence failed; prior file retained if replacement did not complete.") from None
