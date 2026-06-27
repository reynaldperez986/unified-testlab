"""Add is_global and formula columns to the data table."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("recorder", "0006_session_meta_baseline"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE data ADD COLUMN IF NOT EXISTS is_global BOOLEAN NOT NULL DEFAULT FALSE;",
            reverse_sql="ALTER TABLE data DROP COLUMN IF EXISTS is_global;",
        ),
        migrations.RunSQL(
            sql="ALTER TABLE data ADD COLUMN IF NOT EXISTS formula TEXT;",
            reverse_sql="ALTER TABLE data DROP COLUMN IF EXISTS formula;",
        ),
    ]
