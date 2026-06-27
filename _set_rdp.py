"""One-shot: ensures chrome.remote_debugging_port = 9222 in app_config."""
import psycopg2

DSN = "host=localhost port=5432 dbname=automation_db user=postgres password=password"

conn = psycopg2.connect(DSN)
conn.autocommit = True
with conn.cursor() as c:
    c.execute(
        "INSERT INTO app_config (key, value, label, description, group_name, input_type, choices) "
        "VALUES ('chrome.remote_debugging_port', '9222', "
        "        'Remote Debugging Port', "
        "        'CDP port so Add Step can attach to the running Chrome window', "
        "        'chrome', 'text', '') "
        "ON CONFLICT (key) DO UPDATE SET value = '9222'"
    )
    print("upsert status:", c.statusmessage)
conn.close()
print("connection closed and committed.")

# Re-open to verify
conn2 = psycopg2.connect(DSN)
conn2.autocommit = True
with conn2.cursor() as c:
    c.execute("SELECT key, value FROM app_config WHERE key = 'chrome.remote_debugging_port'")
    row = c.fetchone()
    if row:
        print(f"VERIFIED: {row[0]} = {row[1]}")
    else:
        print("ERROR: row not found after re-connect!")
conn2.close()
