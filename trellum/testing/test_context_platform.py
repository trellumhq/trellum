"""Ordinary lifecycle runs on Windows CI too, without symlink privileges."""
import os
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from trellum.agent.agentremoval import incomplete, remove
from trellum.agent.agentsetup import write_project_files
from trellum.scaffold import context_ownership as ownership
from trellum.scaffold import context_windows
from trellum.scaffold.project_context import FILES


@pytest.fixture(params=['native', 'path-dispatch'])
def backend(request, monkeypatch):
    if request.param == 'path-dispatch' and os.name != 'nt':
        # Exercise Windows path dispatch on Linux; kernel semantics remain a
        # native Windows CI obligation. POSIX no-follow handles model refusal.
        @contextmanager
        def handle(path, *, directory=False):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if directory:
                flags |= os.O_DIRECTORY
            fd = os.open(path, flags)
            try:
                yield fd
            finally:
                os.close(fd)
        monkeypatch.setattr(ownership, '_WINDOWS', True)
        monkeypatch.setattr(context_windows, '_handle', handle)
        monkeypatch.setattr(context_windows, '_duplicate', os.dup)
        monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(open_osfhandle=lambda fd, flags: fd))
        monkeypatch.setattr(os, 'O_BINARY', 0, raising=False)


def test_normal_setup_rerun_and_empty_removal(tmp_path, backend):
    assert not incomplete(remove(tmp_path))
    for _ in range(2):
        rows = write_project_files(tmp_path)
        assert not any(status == 'unreadable' for _, status in rows)
        for name, content in FILES.items():
            assert (tmp_path / name).read_bytes() == content.encode()
        record = (tmp_path / ownership.RECORD).read_bytes()
        write_project_files(tmp_path)
        assert (tmp_path / ownership.RECORD).read_bytes() == record
        assert not incomplete(remove(tmp_path))
        assert list(tmp_path.iterdir()) == []
        assert not incomplete(remove(tmp_path))


def test_authored_content_results_and_original_identity(tmp_path, backend):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    preexisting = tmp_path / 'context/metrics.md'
    preexisting.parent.mkdir()
    preexisting.write_bytes(FILES['context/metrics.md'].encode())
    write_project_files(tmp_path)
    assert 'context/metrics.md' not in ownership.load(tmp_path)['files']
    original = ownership.load(tmp_path)['files']['context/warehouse.md']
    note = tmp_path / 'context/warehouse.md'
    note.write_bytes(b'Verified grain\r\n')
    query = tmp_path / 'analysis/check.sql'
    query.write_bytes(b'SELECT 1;\n')
    export = tmp_path / 'analysis/results/check.csv'
    export.write_bytes(b'one\n1\n')
    for _ in range(2):
        write_project_files(tmp_path)
        assert ownership.load(tmp_path)['files']['context/warehouse.md'] == original
        remove(tmp_path)
        assert note.read_bytes() == b'Verified grain\r\n'
        assert query.read_bytes() == b'SELECT 1;\n'
        assert export.read_bytes() == b'one\n1\n'
        assert preexisting.read_bytes() == FILES['context/metrics.md'].encode()
        assert (tmp_path / ownership.IGNORE).read_bytes() == FILES[ownership.IGNORE].encode()
        assert subprocess.run(['git', 'check-ignore', '-q', 'analysis/results/check.csv'], cwd=tmp_path).returncode == 0
        assert subprocess.run(['git', 'check-ignore', '-q', 'analysis/check.sql'], cwd=tmp_path).returncode == 1


def test_malformed_metadata_retained(tmp_path, backend):
    write_project_files(tmp_path)
    record = tmp_path / ownership.RECORD
    record.write_bytes(b'{broken')
    assert ownership.seed(tmp_path) == [(ownership.RECORD, 'unreadable')]
    assert incomplete(remove(tmp_path))
    assert record.read_bytes() == b'{broken'
    assert (tmp_path / 'context/warehouse.md').exists()


def test_owned_identity_survives_template_change(tmp_path, backend, monkeypatch):
    write_project_files(tmp_path)
    digest = ownership.load(tmp_path)['files']['context/warehouse.md']
    (tmp_path / 'context/warehouse.md').unlink()
    monkeypatch.setitem(FILES, 'context/warehouse.md', 'Updated template\n')
    write_project_files(tmp_path)
    assert ownership.load(tmp_path)['files']['context/warehouse.md'] == digest
    remove(tmp_path)
    assert (tmp_path / 'context/warehouse.md').read_bytes() == b'Updated template\n'


def test_nonregular_metadata_is_unreadable(tmp_path, backend):
    (tmp_path / ownership.RECORD).mkdir(parents=True)
    assert ownership.seed(tmp_path) == [(ownership.RECORD, 'unreadable')]
    assert incomplete(remove(tmp_path))


@pytest.mark.parametrize('name', ['context', 'analysis/results', '.trellum'])
def test_linked_directories_are_refused(tmp_path, backend, name):
    root = tmp_path / 'project'
    root.mkdir()
    write_project_files(root)
    path = root / name
    outside = tmp_path / 'outside'
    path.rename(outside)
    before = {p.relative_to(outside): p.read_bytes() for p in outside.rglob('*') if p.is_file()}
    if os.name == 'nt':
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(path), str(outside)], check=True, capture_output=True)
    else:
        path.symlink_to(outside, target_is_directory=True)
    try:
        rows = ownership.seed(root)
        assert any(status == 'unreadable' for _, status in rows)
        assert incomplete(remove(root))
        assert {p.relative_to(outside): p.read_bytes() for p in outside.rglob('*') if p.is_file()} == before
    finally:
        path.rmdir() if os.name == 'nt' else path.unlink()


def test_windows_directory_pin_blocks_ancestor_replacement(tmp_path, backend):
    if os.name == 'nt':
        root = tmp_path / 'project'
        root.mkdir()
        (root / 'context').mkdir()
        with ownership.directory(root, 'context'):
            with pytest.raises(OSError):
                root.rename(tmp_path / 'moved')
            with pytest.raises(OSError):
                (root / 'context').rename(root / 'replaced')
        root.rename(tmp_path / 'moved')


@pytest.mark.parametrize(('attributes', 'directory', 'file_type', 'safe'), [
    (0, False, 1, True), (0x10, True, 1, True),
    (0x400, False, 1, False), (0x410, True, 1, False),
    (0x10, False, 1, False), (0, True, 1, False), (0, False, 2, False),
])
def test_windows_handle_checks_type_and_reparse_tag(tmp_path, monkeypatch, attributes, directory, file_type, safe):
    calls = []
    closed = []
    def information(handle, kind, output, size):
        assert kind == 9  # FileAttributeTagInfo
        output[0] = attributes
        return True
    kernel = SimpleNamespace(
        CreateFileW=lambda *args: calls.append(args) or 123,
        GetFileInformationByHandleEx=information,
        GetFileType=lambda handle: file_type,
        CloseHandle=closed.append,
    )
    monkeypatch.setattr(context_windows, '_kernel', lambda: kernel)
    if safe:
        with context_windows._handle(tmp_path, directory=directory) as handle:
            assert handle == 123
    else:
        with pytest.raises(OSError), context_windows._handle(tmp_path, directory=directory):
            pytest.fail('unsafe handle yielded')
    assert closed == [123]
    assert calls[0][2] & 4 == 0  # Never share deletion/replacement.
    assert calls[0][5] & 0x00200000  # OPEN_REPARSE_POINT


def test_project_initializer_installs_removable_context(tmp_path, backend):
    from trellum.scaffold.init_project import init_project

    init_project(str(tmp_path), non_interactive=True)
    for name, content in FILES.items():
        assert (tmp_path / name).read_bytes() == content.encode()
    before = (tmp_path / ownership.RECORD).read_bytes()
    init_project(str(tmp_path), non_interactive=True)
    assert (tmp_path / ownership.RECORD).read_bytes() == before
    assert not incomplete(remove(tmp_path))
    assert not any((tmp_path / name).exists() for name in FILES)
    assert not incomplete(remove(tmp_path))
