"""Opt-in portal -> framework -> disposable itest database regressions.

Run with scripts/test.py local --heavy --workers 1 <this file>.
Only the two existing itest services are used; each case owns its Compose
project, ephemeral loopback port and volumes, including on startup failure.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import uuid

import pytest
import yaml

from apps.datasources.materialize import materialize
from apps.datasources.models import DataSource

pytestmark = [pytest.mark.django_db, pytest.mark.heavy]
ROOT = Path(__file__).resolve().parents[3]
ITEST = ROOT / "trellum" / "itest"


@pytest.fixture(params=["postgres", "mysql"])
def live_database(request, tmp_path, monkeypatch):
    # Follow itest's flat-module convention and reuse its connection defaults.
    monkeypatch.syspath_prepend(str(ITEST))
    from engines import MYSQL, POSTGRES

    spec = {"postgres": POSTGRES, "mysql": MYSQL}[request.param]
    project = f"trellum-portal-seam-{uuid.uuid4().hex[:12]}"
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": os.defpath, "HOME": str(home),
           f"ITEST_{spec.name.upper()}_PORT": "127.0.0.1:0"}
    # Keep the existing image, healthcheck and synthetic credentials. Bound
    # resource use without changing the shared harness or another worker.
    override = tmp_path / "limits.yaml"
    override.write_text(yaml.safe_dump({"services": {spec.compose_service: {
        "cpus": 1, "mem_limit": "768m", "pids_limit": 256,
    }}}), encoding="utf-8")
    command = ["docker", "compose", "-f", str(ITEST / "docker-compose.yml"),
               "-f", str(override), "-p", project]

    def compose(*args, timeout=180):
        result = subprocess.run(command + list(args), env=env, capture_output=True,
                                text=True, timeout=timeout, check=False)
        # Compose output is deliberately not included: errors can echo env.
        assert result.returncode == 0, f"Compose {args[0]} failed for {project}"
        return result.stdout.strip()

    try:
        compose("up", "-d", "--wait", "--wait-timeout", "120", spec.compose_service)
        address = compose("port", spec.compose_service,
                          "5432" if spec.name == "postgres" else "3306")
        assert address.startswith("127.0.0.1:")
        with patch.dict(os.environ, {
            f"ITEST_{spec.name.upper()}_HOST": "127.0.0.1",
            f"ITEST_{spec.name.upper()}_PORT": address.rsplit(":", 1)[1],
        }, clear=True):
            info = spec.conn_info()
        yield spec, info, home
    finally:
        compose("down", "-v", "--timeout", "10", timeout=60)
        assert compose("ps", "-aq") == "", f"containers remain for {project}"


_QUERY = """
import json
import os
import sys

try:
    from trellum import connect, query
    from trellum.data.datasource_config import resolve_to_legacy_format
    from trellum.data.resolvers import resolve_credentials
    from engines import POSTGRES, MYSQL

    payload = json.load(sys.stdin)
    assert 'TRELLUM_SEAM_AMBIENT_SECRET' not in os.environ
    source = resolve_to_legacy_format('warehouse')
    assert source['type'] == payload['engine']
    assert resolve_credentials(source) == payload['info']
    spec = {'postgres': POSTGRES, 'mysql': MYSQL}[payload['engine']]
    conn = connect('warehouse')
    try:
        spec.seed(conn)
    finally:
        conn.close()
    # Cache is off: the rows must come back from a newly resolved connection.
    frame = query('warehouse', 'SELECT id, label FROM contract WHERE label = :who',
                  {'who': "O'Brien"}, cache_ttl=0)
    assert frame.to_dict('records') == [{'id': 3, 'label': "O'Brien"}]
    database_sql = ('current_database()' if spec.name == 'postgres' else 'DATABASE()')
    identity = query('warehouse', 'SELECT ' + database_sql + ' AS db', cache_ttl=0)
    assert identity.iloc[0]['db'] == payload['info']['database']
    try:
        connect('missing_source')
    except KeyError:
        pass
    else:
        raise AssertionError('unknown source resolved')
    print('live seam passed: ' + spec.name)
except Exception as exc:
    # Never print connection dictionaries, environment or driver messages.
    print('live seam failed: ' + type(exc).__name__, file=sys.stderr)
    sys.exit(1)
"""


def test_materialized_source_queries_itest_database(
    studio_tree, live_database, monkeypatch,
):
    spec, info, home = live_database
    source = DataSource.objects.create(
        studio=studio_tree, name="warehouse", type=spec.name,
        config={key: info[key] for key in ("host", "port", "database")},
        credentials={key: info[key] for key in ("user", "password")},
    )
    monkeypatch.setenv("TRELLUM_SEAM_AMBIENT_SECRET", "synthetic-parent-only")
    before = dict(os.environ)
    injected = materialize(studio_tree)
    assert os.environ == before
    config_path = studio_tree.datasources_dir / "config.yaml"
    entry = yaml.safe_load(config_path.read_text(encoding="utf-8"))["sources"]["warehouse"]
    assert entry["credentials"] == {"local": source.env_prefix}
    assert not {"user", "password"} & entry.keys()
    assert info["password"] not in config_path.read_text(encoding="utf-8")
    assert injected[f"{source.env_prefix}_PASS"] == info["password"]

    # Model the worker's child-process boundary with an allowlist, never a
    # copy of the caller's provider/database credentials or home directory.
    env = {"PATH": os.defpath, "HOME": str(home),
           "FW_PROJECT_ROOT": str(studio_tree.project_root),
           "PYTHONPATH": os.pathsep.join([str(ROOT), str(ITEST)]), **injected}
    result = subprocess.run(
        [sys.executable, "-c", _QUERY], cwd=home, env=env,
        input=json.dumps({"engine": spec.name, "info": info}),
        capture_output=True, text=True, timeout=90, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith(f"live seam passed: {spec.name}")
    assert os.environ == before
