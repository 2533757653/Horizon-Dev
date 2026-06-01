# Kline Cache with Lazy Loading - Design

> **Date:** 2026-06-01
> **Status:** Approved

## Overview

Implement a unified Kline cache system using CryptoCompare as the data source. The cache uses lazy loading with pickle persistence - no background threads, no global refresh. Data is fetched on-demand and cached locally for fast subsequent access.

## Architecture

### Components

1. **CryptoCompareAdapter** (`horizon/internal/datasource/cryptocompare.py`)
   - Fetches OHLCV klines from CryptoCompare API
   - Aggregates data from major exchanges (Binance, Coinbase, Kraken, etc.)
   - Returns candles in standard format: `[{time, open, high, low, close, volume}, ...]`

2. **KlineCache** (`horizon/internal/datasource/cache.py`)
   - Manages in-memory cache with pickle persistence
   - Stores: `{symbol: {timeframe: [(time, open, high, low, close, volume), ...]}}`
   - Implements lazy loading with configurable stale time (default 1 hour)
   - Persists to `$DATA_DIR/kline_cache.pkl`

3. **DataSourceRegistry** (`horizon/internal/datasource/registry.py`)
   - Registers available data source adapters
   - Provides single interface for kline fetching
   - Can fall back to Binance direct if CryptoCompare fails

### Data Flow

```
API Request (/api/kline/{symbol}?timeframe=1h&refresh=false)
    ↓
KlineCache.get_candles(symbol, timeframe, force_refresh)
    ↓
[Cache hit + not stale] → Return cached data
    ↓
[Cache miss + stale] → Fetch from CryptoCompare → Update cache → Return data
    ↓
Save to pickle file
```

## File Structure

```
horizon/
├── internal/
│   └── datasource/
│       ├── __init__.py
│       ├── registry.py         # DataSourceRegistry
│       ├── cache.py             # KlineCache
│       └── cryptocompare.py    # CryptoCompareAdapter
└── data/
    └── kline_cache.pkl         # Persisted cache
```

## Key Design Decisions

### 1. Lazy Loading (No Background Thread)
- Cache loaded from pickle on first API request
- Only fetches new data when:
  - Cache doesn't exist for symbol/timeframe
  - Cache is stale (default 1 hour)
  - `refresh=true` query parameter is set
- No automatic global refresh - user controls when to update

### 2. Pickle Persistence
- Cache persisted to `data/kline_cache.pkl`
- Fast startup - instant data availability after restart
- On startup, cache is available immediately (stale data acceptable)
- Updated after each successful fetch

### 3. Standardized Kline Format
```python
candles = [
    {
        "time": 1234567890,      # Unix timestamp (seconds)
        "open": 50000.0,
        "high": 50100.0,
        "low": 49900.0,
        "close": 50050.0,
        "volume": 123.45
    },
    ...
]
```

### 4. Supported Timeframes
- `1m`, `5m`, `15m`, `1h`, `4h`, `1d`
- Default: `1h`

### 5. Cache Scope
- ~30-40 symbols (configurable watchlist)
- 4 timeframes per symbol (1m, 15m, 1h, 1d for analysis; 8h optional)
- Estimated storage: ~4MB for full cache

## API Integration

### Web Server Changes
- `/api/kline/{symbol}` endpoint already exists
- Modify to use `KlineCache` instead of direct adapter.fetch_klines()
- Add query parameters: `timeframe`, `limit`, `refresh`

### Request Example
```
GET /api/kline/BTC-USDT?timeframe=1h&limit=500&refresh=false
```

### Response (unchanged)
```json
{
  "symbol": "BTC/USDT",
  "timeframe": "1h",
  "exchange": "aggregated",
  "candles": [...]
}
```

## Configuration

```yaml
datasource:
  cryptocompare:
    api_key: ""  # Optional, higher rate limits with key
    base_url: "https://min-api.cryptocompare.com"

cache:
  stale_seconds: 3600  # 1 hour
  cache_file: "./data/kline_cache.pkl"
  symbols:  # Watchlist
    - "BTC/USDT"
    - "ETH/USDT"
    - "SOL/USDT"
    # ... ~40 symbols
```

## Error Handling

1. **CryptoCompare API failure:**
   - Log error, return stale cache if available
   - If no cache, try fallback to Binance direct (for Binance pairs)

2. **Pickle load failure:**
   - Start with empty cache, fetch fresh data

3. **Rate limiting:**
   - Implement simple backoff
   - Cache what we have even on partial failure

## Implementation Order

1. **KlineCache class** - Core cache management with pickle persistence
2. **CryptoCompareAdapter** - API client for CryptoCompare
3. **DataSourceRegistry** - Registry and fallback logic
4. **Web server integration** - Modify `/api/kline` to use cache
5. **Configuration** - Add datasource config to settings

## Testing

1. Unit test KlineCache - pickle save/load, stale detection
2. Integration test CryptoCompareAdapter - verify data format
3. API test - verify cache hit/miss behavior