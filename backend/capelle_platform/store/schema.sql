-- =====================================================================
-- Capelle Platform — Postgres schema bootstrap.
--
-- Loaded verbatim by PostgresStore.initialize() and executed inside a
-- single pool connection. Every statement is idempotent (IF NOT EXISTS)
-- so repeated boots are safe. Keep this file in pure Postgres dialect:
--   • JSONB for structured metadata (lets us use -> / ->> operators)
--   • TIMESTAMPTZ for every timestamp (SQLite stored ISO-8601 strings)
--   • TEXT for IDs (we use 12-char hex; short enough not to need VARCHAR)
--
-- Mirrors the DDL + every CREATE INDEX from impl_sqlite._CREATE_TABLES_SQL
-- plus the idempotent migration DDL in SQLiteStore._migrate().
-- =====================================================================

CREATE TABLE IF NOT EXISTS users (
    id                   TEXT PRIMARY KEY,
    email                TEXT UNIQUE NOT NULL,
    hashed_password      TEXT NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL,
    -- Per-user daily-analysis override. NULL = fall back to the
    -- CAPELLE_DAILY_ANALYSIS_LIMIT default; 0 = unlimited (admins /
    -- heavy users); positive = user-specific cap. Idempotent migration
    -- below handles databases created before this column existed.
    daily_analysis_limit INTEGER,
    -- Entra/Azure AD OIDC SSO linkage (compass-entra-oidc-sso). entra_oid +
    -- entra_tid form the durable identity key for SSO accounts (NULL for
    -- password users); auth_source is 'password' or 'sso'. Idempotent
    -- migrations below carry older databases forward.
    entra_oid            TEXT,
    entra_tid            TEXT,
    auth_source          TEXT NOT NULL DEFAULT 'password',
    -- Email-verification gate (compass-email-verification). FALSE until the
    -- account confirms via the verification link. On a *fresh* DB there are
    -- no legacy accounts, so the column default is FALSE (new password users
    -- must verify); create_user always sets it explicitly. The migration
    -- below adds it DEFAULT TRUE so every PRE-EXISTING row is grandfathered
    -- to verified — see the ADD COLUMN note.
    email_verified       BOOLEAN NOT NULL DEFAULT FALSE
);
-- Idempotent column add for older Postgres databases. Mirrors the
-- SQLite ALTER TABLE guard in SQLiteStore._migrate so a running
-- upgrade picks the column up without requiring a fresh DB.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS daily_analysis_limit INTEGER;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS entra_oid TEXT;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS entra_tid TEXT;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS auth_source TEXT NOT NULL DEFAULT 'password';
-- Grandfather every account that predates email verification: adding the
-- column DEFAULT TRUE backfills all existing rows to verified in one shot.
-- This runs only when the column is first added (IF NOT EXISTS), so it never
-- re-verifies an account on a later boot. New rows are written explicitly by
-- create_user (FALSE for password signups, TRUE for SSO), so the column
-- default governs the one-time backfill only.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT TRUE;

CREATE TABLE IF NOT EXISTS chat_sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    title       TEXT NOT NULL,
    topic       TEXT,
    -- Analysis mode chosen at session creation. NOT NULL with default
    -- preserves the legacy ohrs flow for pre-mode sessions; new rows
    -- always set it explicitly via the handler.
    mode        TEXT NOT NULL DEFAULT 'groeikern',
    -- Session namespace; new and migrated rows use the 'capelle' default.
    domain      TEXT NOT NULL DEFAULT 'capelle',
    deleted_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL
);
-- Idempotent column add for older Postgres databases.
ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'groeikern';
ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS domain TEXT NOT NULL DEFAULT 'capelle';

CREATE TABLE IF NOT EXISTS chat_messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES chat_sessions(id),
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'complete',
    metadata    JSONB,
    created_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS fork_tokens (
    token       TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES chat_sessions(id),
    created_at  TIMESTAMPTZ NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_events (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    session_id  TEXT,
    event_type  TEXT NOT NULL,
    properties  JSONB,
    ts          TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_events_user_ts
    ON usage_events(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_usage_events_type_ts
    ON usage_events(event_type, ts);
CREATE INDEX IF NOT EXISTS idx_usage_events_user_type_ts
    ON usage_events(user_id, event_type, ts);
CREATE INDEX IF NOT EXISTS idx_usage_events_ts
    ON usage_events(ts);

CREATE TABLE IF NOT EXISTS feedback_entries (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    email       TEXT NOT NULL,
    topic       TEXT NOT NULL,
    content     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'new',
    created_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_status_created
    ON feedback_entries(status, created_at);

-- Atomic per-(user, day) daily-quota ledger (STORE-2). Replaces the
-- TOCTOU count of analysis_completed events as the enforcement gate:
-- the enforcer reserves one slot at check time via an UPSERT with a
-- WHERE n < limit guard, and the worker releases it on terminal
-- failure. The composite primary key is the single-row UPSERT target.
CREATE TABLE IF NOT EXISTS daily_quota_counters (
    user_id  TEXT NOT NULL REFERENCES users(id),
    day      TEXT NOT NULL,
    n        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

-- Finding-graph store (GRAPH-1). One row per chat session; the entire
-- graph is stored as a JSONB document (nodes + edges + summary).
-- Additive-only: no existing table is modified.
-- ``version`` is incremented on every mutation for optimistic concurrency.
-- ``updated_at`` is refreshed on every write so consumers can detect
-- changes without reading the full JSONB document.
CREATE TABLE IF NOT EXISTS analysis_graphs (
    session_id  TEXT PRIMARY KEY,
    graph       JSONB NOT NULL,
    version     INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_analysis_graphs_updated_at
    ON analysis_graphs(updated_at);
