"""
Add 'is_baseline' boolean column to session_meta table.
"""

from django.db import migrations


ADD_BASELINE_SQL = """
ALTER TABLE session_meta ADD COLUMN IF NOT EXISTS is_baseline BOOLEAN NOT NULL DEFAULT FALSE;
"""

DROP_BASELINE_SQL = """
ALTER TABLE session_meta DROP COLUMN IF EXISTS is_baseline;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("recorder", "0005_playwright_code_column"),
    ]

    operations = [
        migrations.RunSQL(ADD_BASELINE_SQL, DROP_BASELINE_SQL),
    ]
