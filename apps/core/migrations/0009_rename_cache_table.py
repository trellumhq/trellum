"""Rename the database cache table to match the product name.

0008 created ``bi_cache`` and is left alone: an applied migration describes
what it did, not what we wish it had done. So a fresh install creates the old
name and this migration renames it, which is one extra statement on an empty
table and keeps both paths identical afterwards.

Postgres does not rename a table's indexes with it, so the index and the
primary-key constraint are renamed explicitly. Leaving them would work — a
stale index name breaks nothing — but the next person to read \\d trellum_cache
would have to work out why it disagrees with itself.

Reversible: `migrate core 0008` puts every name back, which matters because the
cache table is the one piece of state a rollback cannot simply discard while
the old code is still reading from it.
"""
from django.db import migrations

FORWARD = """
ALTER TABLE IF EXISTS bi_cache RENAME TO trellum_cache;
ALTER INDEX IF EXISTS bi_cache_expires RENAME TO trellum_cache_expires;
ALTER INDEX IF EXISTS bi_cache_pkey RENAME TO trellum_cache_pkey;
"""

BACKWARD = """
ALTER TABLE IF EXISTS trellum_cache RENAME TO bi_cache;
ALTER INDEX IF EXISTS trellum_cache_expires RENAME TO bi_cache_expires;
ALTER INDEX IF EXISTS trellum_cache_pkey RENAME TO bi_cache_pkey;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_cache_table"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD, reverse_sql=BACKWARD),
    ]
