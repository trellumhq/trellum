"""The database cache table.

Django's documented route is `manage.py createcachetable`, which would be one
more manual step in every install and upgrade — and the kind that is forgotten
until something that needs the cache fails at runtime. A migration means
`migrate` creates it, so scripts/upgrade.sh handles it with everything else.

The DDL matches what createcachetable emits for PostgreSQL. IF NOT EXISTS so an
instance where an operator already ran the command is not broken by this.
"""
from django.db import migrations

TABLE = "bi_cache"

CREATE = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    cache_key varchar(255) NOT NULL PRIMARY KEY,
    value text NOT NULL,
    expires timestamp with time zone NOT NULL
);
CREATE INDEX IF NOT EXISTS {TABLE}_expires ON {TABLE} (expires);
"""

DROP = f"DROP TABLE IF EXISTS {TABLE};"


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_opsstate"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE, reverse_sql=DROP),
    ]
