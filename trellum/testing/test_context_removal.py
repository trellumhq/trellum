"""Ownership, rather than template recognition, controls context cleanup."""
import json
import subprocess

import pytest

from trellum.agent.agentremoval import incomplete, remove
from trellum.agent.agentsetup import write_project_files
from trellum.scaffold import context_ownership as ownership
from trellum.scaffold.project_context import FILES


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_clean_roundtrip_and_repeated_removal(tmp_path):
    for _ in range(2):
        write_project_files(tmp_path)
        before = tree(tmp_path)
        write_project_files(tmp_path)
        assert tree(tmp_path) == before
        assert not incomplete(remove(tmp_path))
        assert list(tmp_path.iterdir()) == []
        assert not incomplete(remove(tmp_path))


@pytest.mark.parametrize('name', FILES)
def test_existing_exact_templates_never_adopted(tmp_path, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True)
    path.write_bytes(FILES[name].encode())
    parents = [p for p in tmp_path.rglob('*') if p.is_dir()]
    for _ in range(2):
        write_project_files(tmp_path)
        assert name not in ownership.load(tmp_path)['files']
        remove(tmp_path)
        assert path.read_bytes() == FILES[name].encode()
        assert all(p.is_dir() for p in parents)


def test_edited_notes_and_authored_queries_survive_reruns(tmp_path):
    write_project_files(tmp_path)
    record = (tmp_path / ownership.RECORD).read_bytes()
    note = tmp_path / 'context/warehouse.md'
    note.write_bytes(b'Verified grain\r\n\xff')
    query = tmp_path / 'analysis/check.sql'
    query.write_bytes(b'SELECT 1;\r\n')
    write_project_files(tmp_path)
    assert (tmp_path / ownership.RECORD).read_bytes() == record
    for _ in range(2):
        rows = remove(tmp_path)
        assert any(r.name == 'context/warehouse.md' and r.status == 'kept' for r in rows)
        assert note.read_bytes() == b'Verified grain\r\n\xff'
        assert query.read_bytes() == b'SELECT 1;\r\n'
    write_project_files(tmp_path)
    assert note.read_bytes() == b'Verified grain\r\n\xff'
    assert ownership.load(tmp_path)['files']['context/warehouse.md'] == json.loads(record)['files']['context/warehouse.md']


@pytest.mark.parametrize('entry', ['export.csv', '.hidden', 'link', 'folder'])
def test_remaining_results_keep_ignore_until_gone(tmp_path, entry):
    write_project_files(tmp_path)
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    path = tmp_path / 'analysis/results' / entry
    if entry == 'link':
        path.symlink_to('missing')
    elif entry == 'folder':
        path.mkdir()
    else:
        path.write_bytes(b'local evidence')
    rows = remove(tmp_path)
    assert any(r.name == ownership.IGNORE and r.status == 'kept' for r in rows)
    assert (tmp_path / ownership.IGNORE).read_text() == FILES[ownership.IGNORE]
    assert subprocess.run(['git', 'check-ignore', '-q', f'analysis/results/{entry}'], cwd=tmp_path).returncode == 0
    path.rmdir() if entry == 'folder' else path.unlink()
    remove(tmp_path)
    assert not (tmp_path / 'analysis').exists()
    assert not (tmp_path / ownership.RECORD).exists()


@pytest.mark.parametrize('bad', [b'not JSON', b'[]', b'\xff',
    b'{"version":2,"files":{},"directories":[]}',
    b'{"version":1,"files":{"../outside":"' + b'a' * 64 + b'"},"directories":[]}',
    b'{"version":1,"files":{},"directories":["../outside"]}',
    b'{"version":1,"files":{"context/README.md":"wrong"},"directories":[]}'])
def test_bad_record_preserves_every_scaffold_and_is_reported(tmp_path, bad):
    write_project_files(tmp_path)
    (tmp_path / ownership.RECORD).write_bytes(bad)
    before = tree(tmp_path)
    rows = remove(tmp_path)
    assert any(r.name == ownership.RECORD and r.status == 'unreadable' for r in rows)
    for name in (*FILES, ownership.RECORD):
        assert (tmp_path / name).read_bytes() == before[name]
    ownership.seed(tmp_path)
    for name in (*FILES, ownership.RECORD):
        assert (tmp_path / name).read_bytes() == before[name]


@pytest.mark.parametrize('name', ['context', 'analysis', 'analysis/results', '.trellum',
                                  'context/warehouse.md', 'analysis/results/.gitignore', ownership.RECORD])
@pytest.mark.parametrize('after_setup', [False, True])
def test_symlinked_files_and_ancestors_never_followed(tmp_path, name, after_setup):
    root = tmp_path / 'project'
    root.mkdir()
    outside = tmp_path / 'outside'
    if after_setup:
        write_project_files(root)
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    is_dir = name in ownership.DIRECTORIES
    if path.exists():
        path.rename(outside)
    elif is_dir:
        outside.mkdir()
    else:
        outside.write_bytes(b'outside')
    path.symlink_to(outside, target_is_directory=is_dir)
    before = tree(outside) if is_dir else outside.read_bytes()
    write_project_files(root)
    rows = remove(root)
    assert path.is_symlink()
    assert (tree(outside) if is_dir else outside.read_bytes()) == before
    if after_setup:
        assert incomplete(rows)


def test_unreadable_owned_file_is_reported(tmp_path, monkeypatch):
    write_project_files(tmp_path)
    original = ownership.read_file
    def unreadable(fd, name):
        if name == 'warehouse.md':
            raise PermissionError('denied')
        return original(fd, name)
    monkeypatch.setattr(ownership, 'read_file', unreadable)
    rows = remove(tmp_path)
    assert any(r.name == 'context/warehouse.md' and r.status == 'unreadable' for r in rows)
    assert (tmp_path / 'context/warehouse.md').read_text() == FILES['context/warehouse.md']


def test_existing_metadata_and_directories_are_preserved(tmp_path):
    (tmp_path / '.trellum').mkdir()
    (tmp_path / '.trellum/other.json').write_bytes(b'{}')
    (tmp_path / 'analysis').mkdir()
    write_project_files(tmp_path)
    remove(tmp_path)
    assert (tmp_path / '.trellum/other.json').read_bytes() == b'{}'
    assert (tmp_path / 'analysis').is_dir()
    assert not (tmp_path / ownership.RECORD).exists()


def test_interrupted_record_write_does_not_adopt_unrecorded_files(tmp_path, monkeypatch):
    original = ownership.save
    calls = 0
    def interrupted(root, data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('interrupted before ownership committed')
        original(root, data)
    monkeypatch.setattr(ownership, 'save', interrupted)
    assert (ownership.RECORD, 'unreadable') in ownership.seed(tmp_path)
    seeded = tmp_path / 'context/README.md'
    assert seeded.read_text() == FILES['context/README.md']
    monkeypatch.setattr(ownership, 'save', original)
    ownership.seed(tmp_path)
    assert 'context/README.md' not in ownership.load(tmp_path)['files']
    remove(tmp_path)
    assert seeded.read_text() == FILES['context/README.md']


def test_unreadable_record_is_preserved(tmp_path, monkeypatch):
    write_project_files(tmp_path)
    before = tree(tmp_path)
    original = ownership.read_file
    def unreadable(fd, name):
        if name == 'project-context.json':
            raise PermissionError('denied')
        return original(fd, name)
    monkeypatch.setattr(ownership, 'read_file', unreadable)
    assert ownership.seed(tmp_path) == [(ownership.RECORD, 'unreadable')]
    assert incomplete(remove(tmp_path))
    for name in (*FILES, ownership.RECORD):
        assert (tmp_path / name).read_bytes() == before[name]


def test_platform_without_safe_directory_access_preserves_scaffolds(tmp_path, monkeypatch):
    write_project_files(tmp_path)
    before = tree(tmp_path)
    monkeypatch.setattr(ownership.os, 'supports_dir_fd', set())
    assert ownership.seed(tmp_path) == [(ownership.RECORD, 'unreadable')]
    assert incomplete(remove(tmp_path))
    for name in (*FILES, ownership.RECORD):
        assert (tmp_path / name).read_bytes() == before[name]
