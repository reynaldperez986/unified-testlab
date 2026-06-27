import os, django, json
os.environ["DJANGO_SETTINGS_MODULE"] = "webapp.settings"
django.setup()
from django.db import connection

PW_REC = "22453b4f-6717-43f8-8a25-6432bab5dcb8"
SE_REC = "1e73ae3b-4dd9-495b-875d-dac86f57694a"

with connection.cursor() as cur:
    # Latest run for Playwright
    cur.execute(
        "SELECT run_id, step_no, action, status, message, raw_event FROM run_table "
        "WHERE record_id=%s ORDER BY created_at DESC, step_no LIMIT 40",
        [PW_REC]
    )
    rows = cur.fetchall()
    print("=== PLAYWRIGHT RUN ===")
    run_id = rows[0][0] if rows else None
    for r in rows:
        if r[0] != run_id:
            break
        ev = json.loads(r[5]) if r[5] else {}
        msg = (r[4] or "")[:120]
        print(f"  step={r[1]} action={r[2]} status={r[3]} msg={msg}")
        if r[1] == 7:
            print(f"    url: {ev.get('url', '')[:120]}")
            print(f"    selector: {ev.get('selector', '')[:120]}")
            print(f"    xpath: {ev.get('xpath', '')[:120]}")
            print(f"    used_locator: {ev.get('_used_locator', '')[:120]}")
            print(f"    used_strategy: {ev.get('_used_strategy', '')}")

    # Latest run for Selenium
    cur.execute(
        "SELECT run_id, step_no, action, status, message, raw_event FROM run_table "
        "WHERE record_id=%s ORDER BY created_at DESC, step_no LIMIT 40",
        [SE_REC]
    )
    rows = cur.fetchall()
    print("\n=== SELENIUM RUN ===")
    run_id = rows[0][0] if rows else None
    for r in rows:
        if r[0] != run_id:
            break
        ev = json.loads(r[5]) if r[5] else {}
        msg = (r[4] or "")[:120]
        print(f"  step={r[1]} action={r[2]} status={r[3]} msg={msg}")
        if r[1] == 7:
            print(f"    url: {ev.get('url', '')[:120]}")
            print(f"    selector: {ev.get('selector', '')[:120]}")
            print(f"    xpath: {ev.get('xpath', '')[:120]}")
            print(f"    used_locator: {ev.get('_used_locator', '')[:120]}")
            print(f"    used_strategy: {ev.get('_used_strategy', '')}")

    # Also get step 7 from steps table for both
    print("\n=== STEP 7 DEFINITION (from steps table) ===")
    for label, rec in [("Playwright", PW_REC), ("Selenium", SE_REC)]:
        cur.execute(
            "SELECT raw_event FROM steps WHERE record_id=%s AND step_no=7",
            [rec]
        )
        row = cur.fetchone()
        if row:
            ev = json.loads(row[0]) if row[0] else {}
            print(f"\n  [{label}] step 7:")
            print(f"    action: {ev.get('action')}")
            print(f"    url: {ev.get('url', '')[:120]}")
            print(f"    selector: {ev.get('selector', '')[:120]}")
            print(f"    xpath: {ev.get('xpath', '')[:120]}")
            print(f"    id: {ev.get('id', '')}")
            print(f"    tag: {ev.get('tag', '')}")
            print(f"    text: {ev.get('text', '')[:80]}")
            print(f"    className: {ev.get('className', '')[:80]}")
