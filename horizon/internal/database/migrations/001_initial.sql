-- Initial database schema for Horizon Trading Platform
-- Creates all core tables for trading operations

-- Exchanges table: stores exchange configurations and API credentials
CREATE TABLE IF NOT EXISTS exchanges (
    name TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 1,
    api_key_encrypted TEXT,
    api_secret_encrypted TEXT,
    extra_params TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Orders table: tracks all trading orders across exchanges
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type TEXT NOT NULL CHECK (order_type IN ('market', 'limit')),
    price REAL,
    volume REAL NOT NULL,
    filled_volume REAL DEFAULT 0.0,
    status TEXT NOT NULL CHECK (status IN ('pending', 'submitted', 'partially_filled', 'filled', 'cancelled', 'rejected', 'expired')),
    source TEXT NOT NULL CHECK (source IN ('manual', 'llm_proposal', 'autonomous')),
    proposal_id TEXT,
    exchange_order_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (exchange) REFERENCES exchanges(name)
);

-- Order events table: immutable event log for order state changes
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('created', 'submitted', 'filled', 'partial_fill', 'cancelled', 'rejected', 'guardrail_blocked')),
    event_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

-- Portfolio snapshots table: periodic balance snapshots for performance tracking
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    asset TEXT NOT NULL,
    free_balance REAL NOT NULL,
    locked_balance REAL NOT NULL,
    usdt_value REAL,
    snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Market data cache table: latest market data for quick access
CREATE TABLE IF NOT EXISTS market_data_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    last_price REAL,
    bid_price REAL,
    ask_price REAL,
    volume_24h REAL,
    orderbook_bids TEXT,
    orderbook_asks TEXT,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(exchange, symbol)
);