-- Exchange symbols table: stores all tradeable symbols from each exchange
CREATE TABLE IF NOT EXISTS exchange_symbols (
    exchange     TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    base_asset   TEXT NOT NULL,
    quote_asset  TEXT NOT NULL,
    volume_24h   TEXT,
    price        TEXT,
    last_updated INTEGER NOT NULL,
    PRIMARY KEY (exchange, symbol)
);

-- Active exchanges table: tracks which exchanges are "active" (user-selected for symbol fetching)
CREATE TABLE IF NOT EXISTS active_exchanges (
    exchange TEXT PRIMARY KEY,
    activated_at INTEGER NOT NULL
);