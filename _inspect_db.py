import psycopg2

DSN = "host=localhost port=5432 dbname=automation_db user=postgres password=password"

conn = psycopg2.connect(DSN)
conn.autocommit = True
with conn.cursor() as c:
    c.execute("SELECT table_type FROM information_schema.tables WHERE table_name='app_config'")
    print("table_type:", c.fetchone())
    c.execute("SELECT rulename FROM pg_rules WHERE tablename='app_config'")
    print("rules:", c.fetchall())
    c.execute("SELECT COUNT(*) FROM app_config")
    print("direct count:", c.fetchone())
conn.close()

import django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webapp.settings")
django.setup()
from django.db import connection
with connection.cursor() as c:
    c.execute("SELECT COUNT(*) FROM app_config")
    print("ORM count:", c.fetchone())
    c.execute("SHOW transaction_isolation")
    print("isolation:", c.fetchone())
    c.execute("SELECT txid_current()")
    print("txid:", c.fetchone())
