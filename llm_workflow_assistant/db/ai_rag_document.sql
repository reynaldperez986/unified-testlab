-- ===========================================================================
-- ai_rag_document  —  RAG vector store for the Workflow Assistant
-- Requires: CREATE EXTENSION IF NOT EXISTS vector;
-- ===========================================================================

-- ── Live table ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ai_rag_document (
    id                  BIGSERIAL       PRIMARY KEY,
    source_type         TEXT            NOT NULL,                       -- steps | ai_workflow | ai_databank
    source_key          TEXT            NOT NULL UNIQUE,                -- e.g. steps:<record_id>:<step_no>
    source_title        TEXT            NOT NULL DEFAULT '',
    document_text       TEXT            NOT NULL DEFAULT '',
    metadata            JSONB           NOT NULL DEFAULT '{}'::jsonb,   -- see "Metadata by source_type" below
    source_updated_at   TIMESTAMPTZ     NULL,
    tenant_id           UUID            NULL,
    content_hash        TEXT            NOT NULL DEFAULT '',
    embedding_model     TEXT            NULL,                           -- e.g. nomic-embed-text
    embedding           vector          NULL,                           -- 768-dim for nomic-embed-text
    needs_embedding     BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE UNIQUE INDEX IF NOT EXISTS ai_rag_document_source_key_uniq
    ON ai_rag_document (source_key);

CREATE INDEX IF NOT EXISTS ai_rag_document_source_type_idx
    ON ai_rag_document (source_type);

CREATE INDEX IF NOT EXISTS ai_rag_document_tenant_idx
    ON ai_rag_document (tenant_id);

CREATE INDEX IF NOT EXISTS ai_rag_document_updated_at_idx
    ON ai_rag_document (updated_at DESC);

CREATE INDEX IF NOT EXISTS ai_rag_document_embedding_state_idx
    ON ai_rag_document (needs_embedding, embedding_model);

CREATE INDEX IF NOT EXISTS ai_rag_document_fts_idx
    ON ai_rag_document
    USING GIN (to_tsvector('simple', COALESCE(source_title, '') || ' ' || COALESCE(document_text, '')));


-- ── Staging table (identical schema, used during zero-downtime rebuilds) ────

CREATE TABLE IF NOT EXISTS ai_rag_document_staging (
    id                  BIGSERIAL       PRIMARY KEY,
    source_type         TEXT            NOT NULL,
    source_key          TEXT            NOT NULL UNIQUE,
    source_title        TEXT            NOT NULL DEFAULT '',
    document_text       TEXT            NOT NULL DEFAULT '',
    metadata            JSONB           NOT NULL DEFAULT '{}'::jsonb,
    source_updated_at   TIMESTAMPTZ     NULL,
    tenant_id           UUID            NULL,
    content_hash        TEXT            NOT NULL DEFAULT '',
    embedding_model     TEXT            NULL,
    embedding           vector          NULL,
    needs_embedding     BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ai_rag_document_staging_source_key_uniq
    ON ai_rag_document_staging (source_key);

CREATE INDEX IF NOT EXISTS ai_rag_document_staging_source_type_idx
    ON ai_rag_document_staging (source_type);

CREATE INDEX IF NOT EXISTS ai_rag_document_staging_tenant_idx
    ON ai_rag_document_staging (tenant_id);

CREATE INDEX IF NOT EXISTS ai_rag_document_staging_embedding_state_idx
    ON ai_rag_document_staging (needs_embedding, embedding_model);


-- ── Sync state table ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ai_rag_sync_state (
    sync_key            TEXT            PRIMARY KEY,
    status              TEXT            NOT NULL DEFAULT 'idle',        -- idle | queued | running
    last_started_at     TIMESTAMPTZ     NULL,
    last_finished_at    TIMESTAMPTZ     NULL,
    last_error          TEXT            NULL,
    indexed_documents   BIGINT          NOT NULL DEFAULT 0,
    pending_documents   BIGINT          NOT NULL DEFAULT 0,
    target_documents    BIGINT          NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);


-- ===========================================================================
-- Metadata by source_type  (stored in the JSONB `metadata` column)
-- ===========================================================================
--
-- source_type = 'steps'  (from document_builder._build_step_documents)
-- ┌──────────────┬──────────────────────────────────────────────────────────┐
-- │ key          │ description                                              │
-- ├──────────────┼──────────────────────────────────────────────────────────┤
-- │ record_id    │ steps.record_id (UUID)                                   │
-- │ record_name  │ session_meta.record_name (falls back to record_id)       │
-- │ step_no      │ steps.step_no                                            │
-- │ action       │ steps.action  (click, type, navigate, …)                 │
-- │ page_url     │ steps.page_url                                           │
-- │ element_tag  │ steps.element_tag  (input, button, a, …)                 │
-- │ field_name   │ steps.field_name                                         │
-- │ field_value  │ steps.field_value                                        │
-- │ raw_event    │ steps.raw_event  (full JSONB event object)               │
-- │ locators_raw │ steps.locators_raw  (full JSONB locator strategies)      │
-- │ tenant_id    │ steps.tenant_id (UUID)                                   │
-- │ citation     │ "steps:<record_name>#step-<step_no>"                     │
-- └──────────────┴──────────────────────────────────────────────────────────┘
--
-- source_type = 'ai_workflow'  (from document_builder._build_workflow_documents)
-- ┌──────────────────┬──────────────────────────────────────────────────────┐
-- │ key              │ description                                          │
-- ├──────────────────┼──────────────────────────────────────────────────────┤
-- │ workflow_name    │ ai_workflow.workflow_name                             │
-- │ page_names       │ list of page names in order                          │
-- │ page_connections │ [{from_page_name, to_page_name}, …]                  │
-- │ page_sequence    │ [{order, page_name}, …]                              │
-- │ view_state       │ ai_workflow.workflow_payload.view_state               │
-- │ citation         │ "ai_workflow:<workflow_name>"                         │
-- └──────────────────┴──────────────────────────────────────────────────────┘
--
-- source_type = 'ai_databank'  (from document_builder._build_databank_documents)
-- ┌──────────────┬──────────────────────────────────────────────────────────┐
-- │ key          │ description                                              │
-- ├──────────────┼──────────────────────────────────────────────────────────┤
-- │ id           │ ai_databank.id                                           │
-- │ page_name    │ ai_databank.page_name                                    │
-- │ page_url     │ ai_databank.page_url                                     │
-- │ element_type │ ai_databank.element_type                                 │
-- │ tag_name     │ locator_property.tag_name                                │
-- │ text         │ locator_property.text                                    │
-- │ locator_keys │ first 12 locator strategy names                          │
-- │ citation     │ "ai_databank:<id>"                                       │
-- └──────────────┴──────────────────────────────────────────────────────────┘