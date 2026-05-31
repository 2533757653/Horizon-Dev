-- Migration 002: Technical Indicators Table
-- Adds technical_indicators table for storing computed technical analysis values

CREATE TABLE IF NOT EXISTS technical_indicators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    rsi_14 REAL,
    macd_line REAL,
    macd_signal REAL,
    macd_histogram REAL,
    ema_20 REAL,
    ema_50 REAL,
    bollinger_upper REAL,
    bollinger_lower REAL,
    atr_14 REAL,
    computed_at TIMESTAMP NOT NULL,
    UNIQUE(symbol, computed_at)
);