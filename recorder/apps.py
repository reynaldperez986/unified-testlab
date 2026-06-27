import sys
from django.apps import AppConfig


def _create_app_tables(sender, connection, **kwargs):
    """Create recorder app tables and run Django migrations on the first DB connection."""
    try:
        # Run Django system migrations if not yet applied (e.g. fresh DB)
        with connection.cursor() as _cur:
            _cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name='django_session';"
            )
            if _cur.fetchone() is None:
                from django.core.management import call_command
                call_command("migrate", "--run-syncdb", verbosity=0, interactive=False)
                # Create default admin user on a fresh database
                try:
                    from django.contrib.auth import get_user_model
                    User = get_user_model()
                    if not User.objects.filter(username="admin").exists():
                        User.objects.create_superuser(
                            username="admin",
                            email="admin@local.com",
                            password="password",
                        )
                        print("[recorder] Created superuser: admin / password", file=sys.stderr)
                except Exception as user_exc:
                    print(f"[recorder] WARNING: could not create admin user: {user_exc}", file=sys.stderr)
    except Exception as exc:
        print(f"[recorder] WARNING: auto-migrate failed: {exc}", file=sys.stderr)

    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS data (
                    id          BIGSERIAL PRIMARY KEY,
                    record_id  UUID        NOT NULL,
                    step_no     INTEGER     NOT NULL,
                    field_name  TEXT,
                    value       TEXT,
                    folder_name TEXT,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cursor.execute("""
                DROP INDEX IF EXISTS data_field_name_unique_idx
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS data_record_id_field_name_idx
                ON data (record_id, field_name)
                WHERE field_name IS NOT NULL AND field_name <> '';
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS data_field_name_lookup_idx
                ON data (field_name)
                WHERE field_name IS NOT NULL AND field_name <> '';
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS locators (
                    id           BIGSERIAL PRIMARY KEY,
                    record_id   UUID        NOT NULL,
                    step_no      INTEGER     NOT NULL,
                    strategy     TEXT        NOT NULL,
                    locator      TEXT        NOT NULL,
                    is_primary   BOOLEAN     NOT NULL DEFAULT FALSE,
                    locator_rank INTEGER,
                    pos_x        FLOAT,
                    pos_y        FLOAT,
                    folder_name  TEXT,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS locators_stat (
                    id           BIGSERIAL PRIMARY KEY,
                    run_id       UUID,
                    record_id    UUID        NOT NULL,
                    step_no      INTEGER     NOT NULL,
                    strategy     TEXT        NOT NULL,
                    locator      TEXT        NOT NULL,
                    is_primary   BOOLEAN     NOT NULL DEFAULT FALSE,
                    locator_rank INTEGER,
                    pos_x        FLOAT,
                    pos_y        FLOAT,
                    action       TEXT,
                    page_url     TEXT,
                    runner       TEXT,
                    author       TEXT,
                    folder_name  TEXT,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS parent_folders (
                    id                BIGSERIAL   PRIMARY KEY,
                    parent_folder_id  UUID        NOT NULL UNIQUE,
                    parent_folder     TEXT        NOT NULL,
                    parent_order      INTEGER     NOT NULL DEFAULT 1,
                    parent_folder_order INTEGER   NOT NULL DEFAULT 1,
                    file_type         TEXT        NOT NULL DEFAULT 'folder'
                                          CHECK (file_type IN ('folder', 'session')),
                    author            TEXT,
                    public            BOOLEAN     NOT NULL DEFAULT FALSE,
                    is_baseline       BOOLEAN     NOT NULL DEFAULT FALSE,
                    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_updated      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            # Rename column if the old name still exists (migration for existing DBs)
            cursor.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'parent_folders' AND column_name = 'parent_file_order'
                    ) THEN
                        ALTER TABLE parent_folders RENAME COLUMN parent_file_order TO parent_folder_order;
                    END IF;
                END$$;
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sub_folders (
                    id                BIGSERIAL   PRIMARY KEY,
                    sub_folder_id     UUID        NOT NULL UNIQUE,
                    sub_folder        TEXT        NOT NULL,
                    sub_folder_parent UUID        NOT NULL,
                    sub_folder_order   INTEGER     NOT NULL DEFAULT 1,
                    file_type         TEXT        NOT NULL DEFAULT 'sub-folder'
                                          CHECK (file_type IN ('sub-folder', 'session')),
                    author            TEXT,
                    public            BOOLEAN     NOT NULL DEFAULT FALSE,
                    is_baseline       BOOLEAN     NOT NULL DEFAULT FALSE,
                    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_updated      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS end_folders (
                    id                BIGSERIAL   PRIMARY KEY,
                    end_folder_id     UUID        NOT NULL UNIQUE,
                    end_folder        TEXT        NOT NULL,
                    end_folder_parent UUID        NOT NULL,
                    end_folder_order  INTEGER     NOT NULL DEFAULT 1,
                    end_file_order    INTEGER     NOT NULL DEFAULT 1,
                    file_type         TEXT        NOT NULL DEFAULT 'end-folder'
                                          CHECK (file_type IN ('end-folder', 'session')),
                    author            TEXT,
                    public            BOOLEAN     NOT NULL DEFAULT FALSE,
                    is_baseline       BOOLEAN     NOT NULL DEFAULT FALSE,
                    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_updated      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS steps (
                    id          BIGSERIAL   PRIMARY KEY,
                    record_id   UUID        NOT NULL,
                    step_no     INTEGER     NOT NULL,
                    steps_description TEXT,
                    page_title  TEXT,
                    action      TEXT        NOT NULL,
                    page_url    TEXT        NOT NULL,
                    element_tag TEXT,
                    locator_id  BIGINT      REFERENCES locators(id),
                    data_id     BIGINT      REFERENCES data(id),
                    raw_event   JSONB       NOT NULL,
                    recorder    TEXT,
                    runner      TEXT,
                    folder_name TEXT,
                    locators_raw JSONB,
                    field_name  TEXT,
                    field_value TEXT,
                    pos_x       FLOAT,
                    pos_y       FLOAT,
                    strategy    TEXT,
                    locator     TEXT,
                    is_primary  BOOLEAN,
                    locator_rank INTEGER,
                    folder_order     INTEGER     NOT NULL DEFAULT 1,
                    file_order       INTEGER     NOT NULL DEFAULT 1,
                    headless_state   BOOLEAN     NOT NULL DEFAULT FALSE,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    author           TEXT,
                    last_updated_by  TEXT,
                    parent_record_id UUID,
                    sub_record_id    UUID,
                    end_record       UUID,
                    file_type        TEXT        NOT NULL DEFAULT 'step'
                                         CHECK (file_type IN ('step', 'folder')),
                    is_baseline      BOOLEAN     NOT NULL DEFAULT FALSE,
                    parent_folder_id UUID,
                    sub_folder_id    UUID,
                    end_folder_id    UUID
                );
            """)
            cursor.execute("ALTER TABLE steps ADD COLUMN IF NOT EXISTS steps_description TEXT")
            cursor.execute("ALTER TABLE steps ADD COLUMN IF NOT EXISTS page_title TEXT")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recordings (
                    id               BIGSERIAL   PRIMARY KEY,
                    record_id        UUID        NOT NULL,
                    step_no          INTEGER     NOT NULL,
                    steps_description TEXT,
                    page_title  TEXT,
                    action           TEXT        NOT NULL,
                    page_url         TEXT        NOT NULL,
                    element_tag      TEXT,
                    locator_id       BIGINT      REFERENCES locators(id),
                    data_id          BIGINT      REFERENCES data(id),
                    raw_event        JSONB       NOT NULL,
                    recorder         TEXT,
                    runner           TEXT,
                    folder_name      TEXT,
                    locators_raw     JSONB,
                    field_name       TEXT,
                    field_value      TEXT,
                    strategy         TEXT,
                    locator          TEXT,
                    is_primary       BOOLEAN,
                    locator_rank     INTEGER,
                    pos_x            FLOAT,
                    pos_y            FLOAT,
                    folder_order     INTEGER     NOT NULL DEFAULT 1,
                    file_order       INTEGER     NOT NULL DEFAULT 1,
                    headless_state   BOOLEAN     NOT NULL DEFAULT FALSE,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    author           TEXT,
                    last_updated_by  TEXT,
                    parent_record_id UUID,
                    sub_record_id    UUID,
                    end_record       UUID,
                    file_type        TEXT        NOT NULL DEFAULT 'step'
                                         CHECK (file_type IN ('step', 'folder')),
                    is_baseline      BOOLEAN     NOT NULL DEFAULT FALSE,
                    parent_folder_id UUID,
                    sub_folder_id    UUID,
                    end_folder_id    UUID
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS run_table (
                    id          BIGSERIAL PRIMARY KEY,
                    run_id      UUID        NOT NULL,
                    record_id  UUID        NOT NULL,
                    step_no     INTEGER     NOT NULL,
                    action      TEXT        NOT NULL,
                    page_url    TEXT        NOT NULL,
                    element_tag TEXT,
                    locator_id  BIGINT      REFERENCES locators(id),
                    data_id     BIGINT      REFERENCES data(id),
                    raw_event   JSONB       NOT NULL,
                    status      TEXT        NOT NULL DEFAULT 'not_executed'
                        CHECK (status IN ('pass', 'fail', 'not_executed')),
                    message          TEXT,
                    author           TEXT,
                    runner           TEXT,
                    run_date         TIMESTAMPTZ,
                    folder_name      TEXT,
                    folder_order     INTEGER     NOT NULL DEFAULT 1,
                    file_order       INTEGER     NOT NULL DEFAULT 1,
                    is_baseline      BOOLEAN     NOT NULL DEFAULT FALSE,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_updated_by  TEXT,
                    parent_record_id UUID,
                    sub_record_id    UUID,
                    end_record       UUID,
                    file_type        TEXT        NOT NULL DEFAULT 'step'
                                         CHECK (file_type IN ('step', 'folder')),
                    parent_folder_id UUID,
                    sub_folder_id    UUID,
                    end_folder_id    UUID,
                    "validation"     TEXT,
                    screenshot       BYTEA
                );
            """)
            cursor.execute("ALTER TABLE run_table ADD COLUMN IF NOT EXISTS steps_description TEXT")
            cursor.execute("ALTER TABLE run_table ADD COLUMN IF NOT EXISTS page_title TEXT")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS session_meta (
                    id               BIGSERIAL   PRIMARY KEY,
                    parent_folder_id UUID,
                    sub_folder_id    UUID,
                    end_folder_id    UUID,
                    record_id        UUID        NOT NULL UNIQUE,
                    record_name      TEXT        NOT NULL DEFAULT '',
                    recorder         TEXT,
                    folder_name      TEXT,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS app_config (
                    key         VARCHAR(100) PRIMARY KEY,
                    value       TEXT NOT NULL DEFAULT '',
                    label       VARCHAR(200) NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    group_name  VARCHAR(100) NOT NULL DEFAULT 'General',
                    input_type  VARCHAR(20)  NOT NULL DEFAULT 'text'
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS project_folders (
                    folder_name  TEXT        PRIMARY KEY,
                    folder_order INTEGER     NOT NULL DEFAULT 1,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_databank (
                    id                  BIGSERIAL   PRIMARY KEY,
                    page_url            TEXT        NOT NULL,
                    page_name           TEXT        NOT NULL DEFAULT '',
                    element_type        VARCHAR(80) NOT NULL DEFAULT 'element',
                    element_fingerprint TEXT        NOT NULL DEFAULT '',
                    locator_property    JSONB       NOT NULL DEFAULT '{}'::jsonb,
                    screenshot_png      BYTEA,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cursor.execute("ALTER TABLE ai_databank ADD COLUMN IF NOT EXISTS element_fingerprint TEXT NOT NULL DEFAULT ''")
            cursor.execute("ALTER TABLE ai_databank ADD COLUMN IF NOT EXISTS screenshot_png BYTEA")
            cursor.execute("ALTER TABLE ai_databank ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
            cursor.execute("CREATE INDEX IF NOT EXISTS ai_databank_page_url_idx ON ai_databank (page_url)")
            cursor.execute("CREATE INDEX IF NOT EXISTS ai_databank_created_at_idx ON ai_databank (created_at DESC)")
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS ai_databank_page_fingerprint_uniq
                ON ai_databank (page_url, element_fingerprint)
                WHERE element_fingerprint IS NOT NULL AND element_fingerprint <> ''
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_workflow (
                    id                BIGSERIAL   PRIMARY KEY,
                    workflow_name     TEXT        NOT NULL,
                    page_connections  JSONB       NOT NULL DEFAULT '[]'::jsonb,
                    page_sequence     JSONB       NOT NULL DEFAULT '[]'::jsonb,
                    workflow_payload  JSONB       NOT NULL DEFAULT '{}'::jsonb,
                    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cursor.execute("ALTER TABLE ai_workflow ADD COLUMN IF NOT EXISTS page_connections JSONB NOT NULL DEFAULT '[]'::jsonb")
            cursor.execute("ALTER TABLE ai_workflow ADD COLUMN IF NOT EXISTS page_sequence JSONB NOT NULL DEFAULT '[]'::jsonb")
            cursor.execute("ALTER TABLE ai_workflow ADD COLUMN IF NOT EXISTS workflow_payload JSONB NOT NULL DEFAULT '{}'::jsonb")
            cursor.execute("ALTER TABLE ai_workflow ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ai_workflow_workflow_name_uniq ON ai_workflow (workflow_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS ai_workflow_updated_at_idx ON ai_workflow (updated_at DESC)")

    except Exception as exc:
        print(f"[recorder] WARNING: could not create app tables: {exc}", file=sys.stderr)

    # remote_executions and remote_targets are in their own block so they are
    # never skipped by a transaction abort caused by an earlier CREATE failure.
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS remote_executions (
                    id          BIGSERIAL   PRIMARY KEY,
                    "user"      VARCHAR(150),
                    remote_ip   VARCHAR(255) NOT NULL,
                    remote_port INTEGER      NOT NULL DEFAULT 8888,
                    record_id   UUID,
                    headless    BOOLEAN      NOT NULL DEFAULT FALSE,
                    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS remote_targets (
                    id          BIGSERIAL    PRIMARY KEY,
                    remote_ip   VARCHAR(255) NOT NULL,
                    remote_port INTEGER      NOT NULL DEFAULT 8888,
                    last_used   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                    CONSTRAINT remote_targets_ip_port_uniq UNIQUE (remote_ip, remote_port)
                );
            """)
    except Exception as exc:
        print(f"[recorder] WARNING: could not create remote tables: {exc}", file=sys.stderr)

    # Upsert config defaults now that app_config exists
    try:
        from recorder.views import _ensure_config_table
        _ensure_config_table()
    except Exception as exc:
        print(f"[recorder] WARNING: could not seed app_config: {exc}", file=sys.stderr)


class RecorderConfig(AppConfig):
    name = "recorder"

    def ready(self):
        from django.db.backends.signals import connection_created
        connection_created.connect(_create_app_tables)

        # Also run immediately at startup so tables exist before the first request
        try:
            from django.db import connection
            _create_app_tables(sender=None, connection=connection)
        except Exception as exc:
            print(f"[recorder] WARNING: startup table creation failed: {exc}", file=sys.stderr)
