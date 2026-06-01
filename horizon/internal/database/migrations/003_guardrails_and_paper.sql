-- Guardrails and Paper Trading Schema
-- Tables for graduated autonomy guardrails system

-- Table 1: guardrail_events - Records when guardrails block or downgrade orders
CREATE TABLE IF NOT EXISTS guardrail_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT,
    proposal_id TEXT,
    rule_name TEXT NOT NULL CHECK (rule_name IN ('asset_whitelist', 'cooldown', 'exchange_exposure', 'position_size', 'order_notional', 'daily_loss_limit')),
    action_taken TEXT CHECK (action_taken IN ('blocked', 'downgrade_autonomy', 'alert_only')),
    request_symbol TEXT,
    request_exchange TEXT,
    request_side TEXT,
    request_volume REAL,
    request_price REAL,
    current_value REAL,
    threshold_value REAL,
    downgrade_active INTEGER DEFAULT 0,
    downgrade_expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(id),
    FOREIGN KEY (proposal_id) REFERENCES proposals(id)
);

-- Table 2: cooldown_tracking - Tracks cooldown periods per exchange/symbol
CREATE TABLE IF NOT EXISTS cooldown_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    last_trade_at TIMESTAMP,
    cooldown_seconds INTEGER NOT NULL,
    UNIQUE(exchange, symbol)
);

-- Table 3: daily_pnl - Daily P&L tracking (realized + paper)
CREATE TABLE IF NOT EXISTS daily_pnl (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    realized_pnl REAL DEFAULT 0.0,
    paper_pnl REAL DEFAULT 0.0,
    autonomous_trades INTEGER DEFAULT 0,
    manual_trades INTEGER DEFAULT 0,
    llm_proposals INTEGER DEFAULT 0,
    updated_at TIMESTAMP
);

-- Table 4: paper_trades - Paper trading simulation trades
CREATE TABLE IF NOT EXISTS paper_trades (
    id TEXT PRIMARY KEY,
    proposal_id TEXT,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type TEXT NOT NULL CHECK (order_type IN ('market', 'limit')),
    price REAL NOT NULL,
    volume REAL NOT NULL,
    notional_value REAL NOT NULL,
    status TEXT CHECK (status IN ('pending', 'filled', 'expired', 'cancelled')),
    filled_at TIMESTAMP,
    paper_pnl REAL DEFAULT 0.0,
    closed_by_side TEXT,
    closed_at TIMESTAMP,
    created_at TIMESTAMP
);

-- Table 5: paper_positions - Current paper trading positions
CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    volume REAL NOT NULL,
    avg_entry_price REAL NOT NULL,
    current_price REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(exchange, symbol, side)
);