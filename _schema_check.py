import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'webapp.settings'
import django
django.setup()
from django.db import connection

tables = ['steps', 'data', 'parent_folders', 'session_meta', 'locators']
cur = connection.cursor()
for t in tables:
    cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name=%s ORDER BY ordinal_position", [t]
    )
    print(f"\n--- {t} ---")
    for row in cur.fetchall():
        print(f"  {row[0]:30s} {row[1]}")
