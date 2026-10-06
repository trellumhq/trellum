"""A spend row outlives the person it was booked to.

Erase-and-export (internal planning ticket #077) needs the ledger to survive its subject:
the money was spent whether or not the account still exists. The same
change was written against the pre-rename ``buddy`` app on main; this is
it carried onto the renamed tables, since that app no longer loads.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assistant', '0003_assistantsession_in_flight_until'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='llmusage',
            name='user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='llm_usage', to=settings.AUTH_USER_MODEL),
        ),
    ]
