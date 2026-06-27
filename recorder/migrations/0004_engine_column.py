"""
Add 'engine' column (varchar(20), nullable) to session_meta, steps, recordings,
data, locators, and run_table to distinguish selenium vs playwright.
"""

from django.db import migrations


ADD_ENGINE_SQL = """
ALTER TABLE session_meta ADD COLUMN IF NOT EXISTS engine VARCHAR(20);
ALTER TABLE steps        ADD COLUMN IF NOT EXISTS engine VARCHAR(20);
ALTER TABLE recordings   ADD COLUMN IF NOT EXISTS engine VARCHAR(20);
ALTER TABLE data         ADD COLUMN IF NOT EXISTS engine VARCHAR(20);
ALTER TABLE locators     ADD COLUMN IF NOT EXISTS engine VARCHAR(20);
ALTER TABLE run_table    ADD COLUMN IF NOT EXISTS engine VARCHAR(20);
"""

DROP_ENGINE_SQL = """
ALTER TABLE session_meta DROP COLUMN IF EXISTS engine;
ALTER TABLE steps        DROP COLUMN IF EXISTS engine;
ALTER TABLE recordings   DROP COLUMN IF EXISTS engine;
ALTER TABLE data         DROP COLUMN IF EXISTS engine;
ALTER TABLE locators     DROP COLUMN IF EXISTS engine;
ALTER TABLE run_table    DROP COLUMN IF EXISTS engine;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("recorder", "0003_chat_messages"),
    ]

    operations = [
        migrations.RunSQL(ADD_ENGINE_SQL, DROP_ENGINE_SQL),
    ]
