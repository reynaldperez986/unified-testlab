import psycopg2

conn = psycopg2.connect(host="localhost", port=5432, dbname="postgres",
                        user="postgres", password="password")
conn.autocommit = True
cur = conn.cursor()
cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", ("automation_db",))
if not cur.fetchone():
    cur.execute('CREATE DATABASE "automation_db"')
    print("Database 'automation_db' created.")
else:
    print("Database 'automation_db' already exists.")
conn.close()
