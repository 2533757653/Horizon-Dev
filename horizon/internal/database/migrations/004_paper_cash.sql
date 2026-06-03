-- Paper Trading Cash Tracking
-- Single-row table to track virtual cash balance for paper trading

CREATE TABLE IF NOT EXISTS paper_cash (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    initial_cash REAL NOT NULL,
    current_cash REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
