"""Actual clone isolation, including relocated file and repository references."""
import hashlib
from pathlib import Path
import pytest

pytestmark = pytest.mark.portal_e2e


def snapshot(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in Path(directory).rglob('*') if path.is_file()}


def test_database_and_files_do_not_leak(owned_runtime):
    from django.contrib.auth import get_user_model
    from django.db import connections
    from apps.studios.models import StudioRepo
    from .fixtures import configure_database
    before = snapshot(owned_runtime.template_data)
    first_db = first_directory = None
    with owned_runtime.unit('first-isolation') as first:
        configure_database(first)
        first_db, first_directory = first.database, first.data_dir
        user = get_user_model().objects.get(email='alpha-admin@example.invalid')
        original_name = user.name
        user.name = 'must not leak'
        user.save(update_fields=['name'])
        (first.data_dir / 'must-not-leak.txt').write_text('isolated')
        import requests
        # Exercise the deployed default WSGI boundary, not just inspect flags.
        too_long = requests.get(first.origin + '/healthz?q=' + 'x' * 5000, timeout=10)
        assert too_long.status_code == 400
        assert all(str(first.data_dir) in url for url in StudioRepo.objects.values_list('repo_url', flat=True))
        assert not any(str(owned_runtime.template_data).encode() in path.read_bytes()
                       for path in first.data_dir.rglob('*') if path.is_file())
        connections.close_all()
    assert not first_directory.exists()
    with owned_runtime.unit('second-isolation') as second:
        configure_database(second)
        assert get_user_model().objects.get(email='alpha-admin@example.invalid').name == original_name
        assert not (second.data_dir / 'must-not-leak.txt').exists()
        assert second.database != first_db and second.data_dir != first_directory
        connections.close_all()
    assert snapshot(owned_runtime.template_data) == before
    import psycopg
    with psycopg.connect(owned_runtime.db_url('postgres'), autocommit=True) as database:
        assert database.execute('SELECT 1 FROM pg_database WHERE datname=%s', (first_db,)).fetchone() is None
    assert not owned_runtime.units


def test_failed_journey_cleans_its_owned_clone(owned_runtime):
    import psycopg
    directory = database_name = server = None
    with pytest.raises(RuntimeError, match='intentional journey failure'):
        with owned_runtime.unit('failure-isolation') as unit:
            directory, database_name, server = unit.data_dir, unit.database, unit.server
            (directory / 'failed-unit-write').write_text('must be removed')
            raise RuntimeError('intentional journey failure')
    assert not directory.exists() and server.poll() is not None
    with psycopg.connect(owned_runtime.db_url('postgres'), autocommit=True) as database:
        assert database.execute('SELECT 1 FROM pg_database WHERE datname=%s', (database_name,)).fetchone() is None
    assert not owned_runtime.units
