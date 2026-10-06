"""Redshift database driver.

Thin subclass of ``PostgreSQLDriver``: psycopg2 speaks the Redshift wire
protocol, so connecting works unchanged, but dialect differences (Redshift's
SQL extensions, DDL, system tables) are not abstracted here.
"""

from __future__ import annotations

from trellum.data.drivers import register_driver
from trellum.data.drivers.postgres import PostgreSQLDriver


class RedshiftDriver(PostgreSQLDriver):

    @property
    def default_port(self) -> int:
        return 5439


register_driver("redshift", RedshiftDriver())
