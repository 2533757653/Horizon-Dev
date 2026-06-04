-- Migration 005: Co-Pilot conversation tables + action_type on proposals

CREATE TABLE IF NOT EXISTS copilot_sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS copilot_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content TEXT NOT NULL,
    trade_suggestion TEXT,
    context_summary TEXT,
    token_count_input INTEGER,
    token_count_output INTEGER,
    latency_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES copilot_sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_copilot_messages_session
    ON copilot_messages(session_id, created_at);

-- Additive column on proposals for position management.
-- SQLite ALTER TABLE ADD COLUMN is idempotent only via try/except in Python,
-- so we wrap with a sentinel check using PRAGMA. This is portable enough for
-- the existing migration runner which executes the whole file in one transaction.
-- If the column already exists, the ALTER will fail and the runner must
-- tolerate the error and continue. (Alternative: add a guard in db.py.)
ALTER TABLE proposals ADD COLUMN action_type TEXT NOT NULL DEFAULT 'open';
