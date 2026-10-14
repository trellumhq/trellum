"""Durable workspace identity and startup-only HTTP identity cache.

Identity continuity assumes a trusted deployment; copying a volume also copies
its marker. The marker does not attest to a host or to other mounted volumes.
"""
import os
from pathlib import Path
import secrets
import stat

MARKER = ".annotation-workspace"
_cached = None


class WorkspaceUnavailable(Exception):
    pass


def workspace_identity(root):
    """Live bounded lookup for explicit background producers only."""
    if not all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_NONBLOCK", "O_DIRECTORY")):
        raise WorkspaceUnavailable()
    root = Path(root)
    temporary = None
    try:
        if not root.is_dir():
            raise WorkspaceUnavailable()
        marker = root / MARKER
        try:
            descriptor = os.open(marker, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        except FileNotFoundError:
            temporary = root / (MARKER + "-" + secrets.token_hex(16))
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(secrets.token_hex(16).encode("ascii") + b"\n")
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, marker)
            except FileExistsError:
                pass
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            descriptor = os.open(marker, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise WorkspaceUnavailable()
            value = source.read(34)
        if len(value) != 33 or value[-1:] != b"\n":
            raise WorkspaceUnavailable()
        decoded = value[:-1].decode("ascii")
        if any(char not in "0123456789abcdef" for char in decoded):
            raise WorkspaceUnavailable()
        return decoded
    except (OSError, UnicodeError) as exc:
        raise WorkspaceUnavailable() from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def initialize_workspace_identity():
    """Explicit web/worker startup; migration and collectstatic do not call it."""
    from django.conf import settings

    global _cached
    configured = str(settings.DATA_DIR)
    try:
        Path(configured).mkdir(parents=True, exist_ok=True)
        value = workspace_identity(configured)
    except (OSError, WorkspaceUnavailable):
        value = None
    _cached = (configured, value)
    return value


def cached_workspace_identity():
    """Memory only; setting changes never cause a request-time filesystem read."""
    from django.conf import settings

    if _cached is None or _cached[0] != str(settings.DATA_DIR):
        return None
    return _cached[1]
