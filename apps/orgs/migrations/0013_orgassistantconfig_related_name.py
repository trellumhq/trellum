# The related_name followed the model rename: org.buddy_config -> org.assistant_config.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0012_rename_orgbuddyconfig_orgassistantconfig'),
    ]

    operations = [
        migrations.AlterField(
            model_name='orgassistantconfig',
            name='org',
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='assistant_config', to='orgs.organization'),
        ),
    ]
