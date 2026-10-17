"""A real child process for the operator measurement tests.

The child connects to this test's database with its own settings module, so
``manage.py operator_measure`` runs exactly as the coordinator runs it.
Setting ``OPERATOR_TEST_CHILD_HANG`` makes it block before Django even starts.
"""
import os
from urllib.parse import quote

import pytest
from django.db import connection


@pytest.fixture
def operator_child(settings, tmp_path, monkeypatch):
    assert connection.vendor == "postgresql", "child process gate requires disposable PostgreSQL"
    settings.DATA_DIR = tmp_path / "data"
    settings.DATA_DIR.mkdir()
    database = connection.settings_dict
    url = (f"postgres://{quote(database['USER'])}:{quote(database['PASSWORD'])}@"
           f"{database['HOST']}:{database['PORT']}/{database['NAME']}")
    module = tmp_path / "operator_child_settings.py"
    module.write_text(
        "from trellum_portal.settings.test import *\nfrom pathlib import Path\nimport os, time\n"
        f"DATA_DIR = Path({str(settings.DATA_DIR)!r})\nBACKUP_DIR = {str(tmp_path / 'backups')!r}\n"
        "DEBUG = True\nTRELLUM_SANDBOX = 'off'\nCLEANUP_ENABLED = False\n"
        "TRELLUM_STORAGE_BACKEND = 'local'\nTRELLUM_REPORT_ACCESS_MODEL = 'proxy'\n"
        "if os.environ.get('OPERATOR_TEST_CHILD_HANG'):\n    time.sleep(120)\n"
    )
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "operator_child_settings")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path) + os.pathsep + str(settings.BASE_DIR))
    return settings.DATA_DIR
