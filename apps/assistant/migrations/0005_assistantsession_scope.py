from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("assistant", "0004_llmusage_user_survives_erasure")]

    operations = [
        migrations.AddField(
            model_name="assistantsession",
            name="scope",
            field=models.CharField(
                choices=[("full", "Full studio"), ("report", "One report")],
                default="full",
                max_length=8,
            ),
        ),
    ]
