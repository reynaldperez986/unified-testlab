import django, os, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'webapp.settings')
django.setup()
from django.db import connection

with connection.cursor() as c:
    # Generated record - locators
    print("=== GENERATED dc3b5629 - Locators ===")
    c.execute("""
        SELECT l.step_no, l.strategy, l.locator, l.is_primary, l.locator_rank
        FROM locators l
        WHERE l.record_id = 'dc3b5629-0a48-4ca7-bb84-d6021f627261'
        ORDER BY l.step_no, l.locator_rank, l.id
        LIMIT 20
    """)
    for row in c.fetchall():
        print(f"  step {row[0]}: {row[1]} = {row[2][:60]} | primary={row[3]} rank={row[4]}")

    # Source record c1bd5c5c - locators
    print("\n=== SOURCE c1bd5c5c (Login Trade) - Locators ===")
    c.execute("""
        SELECT l.step_no, l.strategy, l.locator, l.is_primary, l.locator_rank
        FROM locators l
        WHERE l.record_id = 'c1bd5c5c-4a68-4385-b993-c7c0afe480f7'
        ORDER BY l.step_no, l.locator_rank, l.id
        LIMIT 30
    """)
    for row in c.fetchall():
        print(f"  step {row[0]}: {row[1]} = {row[2][:60]} | primary={row[3]} rank={row[4]}")

    # Check steps.locators_raw for both
    print("\n=== GENERATED dc3b5629 - steps.locators_raw ===")
    c.execute("""
        SELECT step_no, locators_raw
        FROM steps WHERE record_id = 'dc3b5629-0a48-4ca7-bb84-d6021f627261'
        ORDER BY step_no LIMIT 5
    """)
    for row in c.fetchall():
        raw = json.loads(row[1]) if row[1] else {}
        print(f"  step {row[0]}: {list(raw.keys()) if raw else 'EMPTY'}")

    print("\n=== SOURCE c1bd5c5c - steps.locators_raw ===")
    c.execute("""
        SELECT step_no, locators_raw
        FROM steps WHERE record_id = 'c1bd5c5c-4a68-4385-b993-c7c0afe480f7'
        ORDER BY step_no LIMIT 5
    """)
    for row in c.fetchall():
        raw = json.loads(row[1]) if row[1] else {}
        print(f"  step {row[0]}: {list(raw.keys()) if raw else 'EMPTY'}")
