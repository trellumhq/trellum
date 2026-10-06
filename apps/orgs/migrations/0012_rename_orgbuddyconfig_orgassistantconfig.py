"""BA Buddy became the AI assistant. Rename the model and its table.

``RenameModel`` carries the rows across, so an existing install keeps its
configured provider, its encrypted API key and its spend caps. Done before
the product was released, which is the only reason this can be a plain
rename rather than a compatibility shim.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [("orgs", "0011_organization_retention_abandoned_upload_days_and_more")]

    operations = [
        migrations.RenameModel(
            old_name="OrgBuddyConfig",
            new_name="OrgAssistantConfig",
        ),
    ]
