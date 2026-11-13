-- Declarative schema. Runs only on an empty data volume: after editing, `docker compose down -v`.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunks (
    id              BIGSERIAL PRIMARY KEY,
    chunk_id        TEXT NOT NULL UNIQUE,
    source_filename TEXT NOT NULL,
    page_number     INTEGER NOT NULL,
    product         TEXT NOT NULL,
    circular_no     TEXT,
    fiscal_year     TEXT,
    content_type    TEXT NOT NULL CHECK (content_type IN ('text', 'table')),
    chunk_index     INTEGER NOT NULL,
    content         TEXT NOT NULL,
    embedding       vector(384) NOT NULL
);
-- HNSW (vector_cosine_ops) is created by ingest/embed.py after the bulk insert, not here.
CREATE INDEX IF NOT EXISTS chunks_product_idx ON chunks (product);

CREATE TABLE IF NOT EXISTS chargeback_cases (
    case_id              TEXT PRIMARY KEY,
    rrn                  CHAR(12) NOT NULL,
    cardholder_id        TEXT NOT NULL,
    reason_code          TEXT NOT NULL,
    claim_type           TEXT NOT NULL,
    amount               NUMERIC(14, 2) NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('RAISED', 'WITHDRAWN')),
    raised_by            TEXT NOT NULL,
    raised_by_role       TEXT NOT NULL CHECK (raised_by_role = 'issuer_ops'),
    idempotency_key      TEXT NOT NULL UNIQUE,
    representment_due_at TIMESTAMPTZ,
    withdrawn_by         TEXT,
    withdrawn_reason     TEXT,
    withdrawn_at         TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chargeback_cases_rrn_idx ON chargeback_cases (rrn);
-- At most one live case per RRN, enforced by the database, not by the agent.
CREATE UNIQUE INDEX IF NOT EXISTS chargeback_cases_one_open_per_rrn ON chargeback_cases (rrn) WHERE status = 'RAISED';

CREATE TABLE IF NOT EXISTS disputes (
    dispute_id      TEXT PRIMARY KEY,
    rrn             CHAR(12) NOT NULL,
    cardholder_id   TEXT NOT NULL,
    claim_type      TEXT NOT NULL,
    description     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'LODGED' CHECK (status IN ('LODGED', 'UNDER_REVIEW', 'CLOSED')),
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS disputes_rrn_idx ON disputes (rrn);

CREATE TABLE IF NOT EXISTS fraud_referrals (
    referral_id      TEXT PRIMARY KEY,
    rrn              CHAR(12) NOT NULL,
    cardholder_id    TEXT NOT NULL,
    referred_by      TEXT NOT NULL,
    referred_by_role TEXT NOT NULL,
    note             TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED')),
    idempotency_key  TEXT NOT NULL UNIQUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
    id             BIGSERIAL PRIMARY KEY,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor          TEXT NOT NULL,
    role           TEXT NOT NULL,
    tool           TEXT NOT NULL,
    arguments      JSONB NOT NULL,
    outcome        TEXT NOT NULL,
    result_summary TEXT NOT NULL,
    confirmed_by   TEXT
);

CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();
