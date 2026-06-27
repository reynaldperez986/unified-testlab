"""Add calculate_mode column to the data table."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("recorder", "0008_data_category_increments"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE data ADD COLUMN IF NOT EXISTS calculate_mode VARCHAR(50);",
            reverse_sql="ALTER TABLE data DROP COLUMN IF EXISTS calculate_mode;",
        ),
    ]
