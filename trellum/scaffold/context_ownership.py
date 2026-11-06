"""Conservative ownership of project-context scaffolding, never authored data."""
import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager

from trellum.scaffold.project_context import FILES

RECORD = '.trellum/project-context.json'
DIRECTORIES = ('.trellum', 'context', 'analysis', 'analysis/results')
IGNORE = 'analysis/results/.gitignore'
_WINDOWS = os.name == 'nt'
_FLAGS = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)


@contextmanager
def directory(root, name='', created=None):
    """Pin each directory without following symlinks, including during cleanup."""
    if _WINDOWS:
        from trellum.scaffold.context_windows import directory as windows_directory
        with windows_directory(root, name, created) as path:
            yield path
        return
    if not hasattr(os, 'O_NOFOLLOW') or os.open not in os.supports_dir_fd:
        raise OSError('safe directory-relative scaffold access is unavailable')
    fd = os.open(root, _FLAGS)
    try:
        walked = []
        for part in name.split('/') if name else ():
            walked.append(part)
            if created is not None:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
                else:
                    created.append('/'.join(walked))
            child = os.open(part, _FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def read_file(fd, name):
    if not isinstance(fd, int):
        from trellum.scaffold.context_windows import read_file as windows_read
        return windows_read(fd, name)
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('not a regular file')
        data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError('file too large to verify')
        return data


def access(fd, name):
    """Address a leaf beneath a pinned POSIX descriptor or Windows directory."""
    return (name, {'dir_fd': fd}) if isinstance(fd, int) else (fd / name, {})


def delete(fd, name, *, directory=False):
    path, kwargs = access(fd, name)
    if not isinstance(fd, int):
        from trellum.scaffold.context_windows import check
        check(path, directory=directory)
    (os.rmdir if directory else os.unlink)(path, **kwargs)


def create(fd, name, mode):
    path, kwargs = access(fd, name)
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                   | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), mode, **kwargs)


def load(root):
    """Reject the entire record on any unexpected shape, version or path."""
    try:
        with directory(root, '.trellum') as fd:
            data = json.loads(read_file(fd, 'project-context.json'))
    except FileNotFoundError:
        return {'version': 1, 'files': {}, 'directories': []}
    if (not isinstance(data, dict) or set(data) != {'version', 'files', 'directories'}
            or type(data['version']) is not int or data['version'] != 1
            or not isinstance(data['files'], dict)
            or not isinstance(data['directories'], list)
            or any(not isinstance(p, str) or p not in DIRECTORIES for p in data['directories'])
            or len(set(data['directories'])) != len(data['directories'])
            or any(p not in FILES or not isinstance(digest, str)
                   or re.fullmatch('[0-9a-f]{64}', digest) is None
                   for p, digest in data['files'].items())):
        raise ValueError('invalid context ownership record')
    return data


def save(root, data):
    """Replace metadata atomically; an interrupted seed is left unowned, never guessed."""
    with directory(root, '.trellum') as fd:
        temp = f'.project-context-{uuid.uuid4().hex}.tmp'
        try:
            handle = create(fd, temp, 0o600)
            with os.fdopen(handle, 'w', encoding='utf-8') as stream:
                stream.write(json.dumps(data, sort_keys=True, indent=2) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            if isinstance(fd, int):
                os.replace(temp, 'project-context.json', src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
            else:
                os.replace(fd / temp, fd / 'project-context.json')
        finally:
            try:
                delete(fd, temp)
            except FileNotFoundError:
                pass


def seed(root):
    try:
        data = load(root)
    except (OSError, ValueError, UnicodeError):
        return [(RECORD, 'unreadable')]
    rows = []
    created = []
    try:
        with directory(root, '.trellum', created):
            pass
        data['directories'].extend(p for p in created if p not in data['directories'])
        save(root, data)
        for name, content in FILES.items():
            parent, leaf = name.rsplit('/', 1)
            created = []
            try:
                with directory(root, parent, created) as fd:
                    handle = create(fd, leaf, 0o666)
                    with os.fdopen(handle, 'wb') as stream:
                        stream.write(content.encode('utf-8'))
                    # Never replace the initial identity, even after a missing file is reseeded.
                    data['files'].setdefault(name, hashlib.sha256(content.encode('utf-8')).hexdigest())
                    rows.append((name, 'created'))
            except FileExistsError:
                try:
                    with directory(root, parent) as fd:
                        read_file(fd, leaf)
                except (OSError, ValueError):
                    rows.append((name, 'unreadable'))
                else:
                    rows.append((name, 'current'))
            except OSError:
                rows.append((name, 'unreadable'))
            data['directories'].extend(p for p in created if p not in data['directories'])
            save(root, data)
    except OSError:
        rows.append((RECORD, 'unreadable'))
    return rows


def remove_file(root, name, digest):
    parent, leaf = name.rsplit('/', 1)
    try:
        with directory(root, parent) as fd:
            current = read_file(fd, leaf)
            if hashlib.sha256(current).hexdigest() != digest:
                return 'kept', 'edited since setup; left untouched'
            if name == IGNORE and any(entry != leaf for entry in os.listdir(fd)):
                return 'kept', 'local results remain; ignore protection retained'
            delete(fd, leaf)
    except FileNotFoundError:
        return 'absent', ''
    except (OSError, ValueError):
        return 'unreadable', 'unreadable or symlinked scaffold; left untouched'
    return 'deleted', ''


def remove(root):
    """Return Row-compatible outcomes; retain the record while owned work remains."""
    try:
        data = load(root)
    except (OSError, ValueError, UnicodeError):
        return [(RECORD, 'unreadable', 'invalid or unreadable ownership record; scaffolding retained')]
    rows = []
    for name in FILES:  # Paths always come from the fixed allowlist, never metadata.
        if name not in data['files']:
            continue
        status, note = remove_file(root, name, data['files'][name])
        rows.append((name, status, note))
        if status in ('deleted', 'absent'):
            del data['files'][name]
    for name in reversed(DIRECTORIES[1:]):
        if name not in data['directories']:
            continue
        parent, _, leaf = name.rpartition('/')
        try:
            with directory(root, parent) as fd:
                delete(fd, leaf, directory=True)  # Empty only; symlinks cannot be followed.
        except FileNotFoundError:
            pass
        except OSError:
            rows.append((name, 'kept', 'directory is not empty or is unsafe; left untouched'))
            continue
        data['directories'].remove(name)
    try:
        if data['files'] or any(p != '.trellum' for p in data['directories']):
            save(root, data)
        else:
            with directory(root, '.trellum') as fd:
                delete(fd, 'project-context.json')
            if '.trellum' in data['directories']:
                with directory(root) as fd:
                    try:
                        delete(fd, '.trellum', directory=True)
                    except OSError:
                        pass  # Other metadata is never ours to remove.
    except FileNotFoundError:
        pass
    except OSError:
        rows.append((RECORD, 'unreadable', 'could not update ownership record; retained'))
    return rows
