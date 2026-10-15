from django.db import migrations


class Migration(migrations.Migration):
    """Drop the annotation projection and filter-state tables from 0029 and 0030.

    Nothing reads or writes them: annotations are still read from the report
    sources. No released version created these tables, so no earlier code can
    depend on them, and the tables are empty wherever 0029/0030 ran.
    """

    dependencies = [
        ('reports', '0033_source_summary_change_stamp'),
    ]

    # Dependents first, so the model state never holds a dangling relation.
    operations = [
        migrations.DeleteModel(name='AnnotationEvent'),
        migrations.DeleteModel(name='AnnotationFilterMembership'),
        migrations.DeleteModel(name='AnnotationFilterReceipt'),
        migrations.DeleteModel(name='AnnotationFilterState'),
        migrations.DeleteModel(name='AnnotationSourceState'),
        migrations.DeleteModel(name='AnnotationCatalogState'),
    ]
