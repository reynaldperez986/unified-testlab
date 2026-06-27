"""
Multi-tenant + async readiness migration.

Creates:
  - tenants          – one row per isolated organisational unit
  - user_profiles    – links Django auth.User → Tenant

Adds (idempotent via IF NOT EXISTS / DO NOTHING patterns):
  - tenant_id UUID column to steps, run_table, session_meta,
    recordings, data, locators

A "Default" tenant is inserted so that existing rows can be
back-filled in a follow-up data migration or admin action.
"""

from django.db import migrations


_CREATE_TENANTS = """
CREATE TABLE IF NOT EXISTS tenants (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(200) NOT NULL UNIQUE,
    slug        VARCHAR(100) NOT NULL UNIQUE,
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_CREATE_USER_PROFILES = """
CREATE TABLE IF NOT EXISTS user_profiles (
    id        BIGSERIAL   PRIMARY KEY,
    user_id   INTEGER     NOT NULL UNIQUE REFERENCES auth_user(id) ON DELETE CASCADE,
    tenant_id UUID        REFERENCES tenants(id) ON DELETE SET NULL
);
"""

# Idempotent helper — adds a column only if it does not already exist.
_ADD_COL_TEMPLATE = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = '{table}' AND column_name = 'tenant_id'
    ) THEN
        ALTER TABLE {table} ADD COLUMN tenant_id UUID;
    END IF;
END $$;
"""

# Tables that need the tenant_id column
_TENANT_TABLES = [
    "steps",
    "run_table",
    "session_meta",
    "recordings",
    "data",
    "locators",
]

# Index creation (idempotent)
_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_{table}_tenant ON {table}(tenant_id);",
]

_INSERT_DEFAULT_TENANT = """
INSERT INTO tenants (name, slug, is_active, created_at)
VALUES ('Default', 'default', TRUE, NOW())
ON CONFLICT DO NOTHING;
"""


def apply_migration(apps, schema_editor):
    conn = schema_editor.connection
    with conn.cursor() as cur:
        cur.execute(_CREATE_TENANTS)
        cur.execute(_CREATE_USER_PROFILES)
        for table in _TENANT_TABLES:
            cur.execute(_ADD_COL_TEMPLATE.format(table=table))
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_tenant_id "
                f"ON {table}(tenant_id) WHERE tenant_id IS NOT NULL;"
            )
        cur.execute(_INSERT_DEFAULT_TENANT)


def revert_migration(apps, schema_editor):
    conn = schema_editor.connection
    with conn.cursor() as cur:
        for table in _TENANT_TABLES:
            cur.execute(
                f"ALTER TABLE {table} DROP COLUMN IF EXISTS tenant_id;"
            )
        cur.execute("DROP TABLE IF EXISTS user_profiles;")
        cur.execute("DROP TABLE IF EXISTS tenants;")


class Migration(migrations.Migration):

    dependencies = [
        ("recorder", "0001_remote_targets"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(apply_migration, revert_migration),
    ]
