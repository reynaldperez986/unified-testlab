"""
Add 'playwright_code' column (text, nullable) to steps and recordings tables
to store the Playwright code line for each step.
"""

from django.db import migrations


SQL = """
ALTER TABLE steps      ADD COLUMN IF NOT EXISTS playwright_code TEXT;
ALTER TABLE recordings ADD COLUMN IF NOT EXISTS playwright_code TEXT;
"""

REVERSE_SQL = """
ALTER TABLE steps      DROP COLUMN IF EXISTS playwright_code;
ALTER TABLE recordings DROP COLUMN IF EXISTS playwright_code;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("recorder", "0004_engine_column"),
    ]

    operations = [
        migrations.RunSQL(SQL, REVERSE_SQL),
    ]
