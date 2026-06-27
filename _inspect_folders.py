import os, sys
sys.path.insert(0,'.')
os.environ['DJANGO_SETTINGS_MODULE']='webapp.settings'
import django; django.setup()
from django.db import connection
with connection.cursor() as c:
    c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE '%folder%' ORDER BY 1")
    print([r[0] for r in c.fetchall()])
    for t in ['parent_folders','sub_folders','end_folders']:
        c.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_name=%s ORDER BY ordinal_position",[t])
        print(t,':', c.fetchall())
    # also check steps table for folder FK columns
    c.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_name='steps' AND column_name LIKE '%folder%' ORDER BY ordinal_position")
    print('steps folder cols:', c.fetchall())
    # sample data
    for t in ['parent_folders','sub_folders','end_folders']:
        c.execute(f"SELECT * FROM {t} LIMIT 3")
        print(f'{t} sample:', c.fetchall())
