import os, django, json
os.environ["DJANGO_SETTINGS_MODULE"] = "webapp.settings"
django.setup()
from django.db import connection

REC = "03638b86-9976-4c5b-8bf7-9d9e530d3bca"

with connection.cursor() as cur:
    # Check session_meta
    cur.execute("SELECT record_id, record_name, created_at FROM session_meta ORDER BY created_at DESC LIMIT 10")
    print("=== SESSION META (recent) ===")
    for row in cur.fetchall():
        print(row)

    # Check if this record has steps
    cur.execute("SELECT count(*) FROM steps WHERE record_id=%s", [REC])
    print(f"\nSteps count for {REC}: {cur.fetchone()[0]}")

    # Check run_table
    cur.execute("SELECT run_id, step_no, action, status, message FROM run_table WHERE record_id=%s ORDER BY step_no", [REC])
    print(f"\n=== RUN RESULTS for {REC} ===")
    for row in cur.fetchall():
        print(f"  run={str(row[0])[:8]} step={row[1]} action={row[2]} status={row[3]} msg={str(row[4])[:60] if row[4] else ''}")

    # Show most recent session with steps
    cur.execute("SELECT record_id FROM session_meta ORDER BY created_at DESC LIMIT 1")
    latest = cur.fetchone()
    if latest:
        latest_id = str(latest[0])
        cur.execute("SELECT step_no, action, raw_event FROM steps WHERE record_id=%s ORDER BY step_no", [latest_id])
        print(f"\n=== LATEST SESSION {latest_id[:8]} STEPS ===")
        for row in cur.fetchall():
            ev = json.loads(row[2]) if row[2] else {}
            print(f"  step={row[0]} action={row[1]} url={ev.get('url','')[:60]} delay={ev.get('_recorded_step_delay_s','')}")


