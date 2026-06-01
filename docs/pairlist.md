# PairList Design Specification

**Date:** 2026-06-01
**Status:** Draft — pending review

---

## 1. Purpose

Replace the hardcoded `market_data.symbols` config with a **pluggable PairList system**.

**Workflow:**
1. User selects which **exchange(s)** to use → system fetches ALL symbols from those exchanges
2. Symbols are **cached** in the database (`exchange_symbols` table)
3. User selects a **PairList** → PairList reads the cache, filters, returns dynamic coins
4. `MarketDataFetcher` monitors only those symbols
5. User can re-select PairList anytime → gets new dynamic coins
6. Cache silently refreshes every 24 hours

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        User Workflow                                         │
│  Step 1: POST /api/exchanges/active  → select which exchanges to use        │
│  Step 2: POST /api/pairlists/refresh-cache → fetch & cache all symbols      │
│  Step 3: POST /api/pairlists/active  → select PairList, get dynamic coins   │
│  Step 4: (later) POST /api/pairlists/active  → re-select, get new coins     │
│  Step 5: (daily, silent) background task refreshes cache                    │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                        Active PairList (one at a time)                       │
│   Selected via API: POST /api/pairlists/active { "name": "top_coins" }      │
│         └─────────────────────────┬─────────────────────────────────────────┘
│                                   │ .get_pairs(db)
│                                   ▼
│ ┌──────────────────────────────────────────────────────────────────────────┐
│ │                         SymbolCache                                       │
│ │   SQLite table: exchange_symbols                                         │
│ │   ─ exchange          TEXT NOT NULL                                      │
│ │   ─ symbol            TEXT NOT NULL                                      │
│ │   ─ base_asset        TEXT NOT NULL                                      │
│ │   ─ quote_asset       TEXT NOT NULL                                      │
│ │   ─ volume_24h        TEXT (nullable)                                    │
│ │   ─ price             TEXT (nullable)                                    │
│ │   ─ last_updated      INTEGER (epoch ms)                                 │
│ │   PK: (exchange, symbol)                                                 │
│ │   Refresh interval: every 24 hours via background task                  │
│ └──────────────────────────────┬───────────────────────────────────────────┘
│                                ▲
│                                │ populate_cache(active_exchanges)
│                                ▼
│ ┌──────────────────────────────────────────────────────────────────────────┐
│ │              Active Exchanges (subset of enabled)                        │
│ │  User selects which enabled exchanges to fetch symbols from              │
│ │  Each adapter implements: async fetch_all_symbols() -> list[SymbolInfo]  │
│ └──────────────────────────────────────────────────────────────────────────┘
```

---

## 3. PairList Class Interface

Each plugin file in `horizon/pairlists/*.py` defines exactly one class:

```python
class PairList(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique identifier for this PairList, e.g. 'top_coins'."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """Short human-readable description."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_pairs(self, db: aiosqlite.Connection) -> list[str]:
        """
        Read from the exchange_symbols cache, filter, and return the
        list of generic-format symbols (e.g. ['BTC/USDT', 'ETH/USDT']).
        """
        raise NotImplementedError
```

### Rules

- A plugin file may contain helper methods, but **must expose exactly one `PairList` subclass**.
- `get_pairs()` is **read-only** on the database. It must not write.
- `get_pairs()` is called by `MarketDataFetcher` every poll cycle. It must be fast (cached table lookups only).
- PairList parameters (volume threshold, quote asset, etc.) are **hardcoded in the file**.

---

## 4. Exchange Symbol Cache

### 4.1 Database Table

```sql
CREATE TABLE IF NOT EXISTS exchange_symbols (
    exchange    TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    base_asset  TEXT NOT NULL,
    quote_asset TEXT NOT NULL,
    volume_24h  TEXT,
    price       TEXT,
    last_updated INTEGER NOT NULL,
    PRIMARY KEY (exchange, symbol)
);
```

**Notes:**

- `volume_24h` and `price` are stored as `TEXT` to preserve Decimal precision.
- `last_updated` is Unix epoch milliseconds. Used to invalidate stale entries (older than 25h → treat as missing).
- Deletions: on each refresh, symbols no longer returned by the exchange are removed from the cache.

### 4.2 SymbolInfo Dataclass

```python
@dataclass(frozen=True)
class SymbolInfo:
    exchange: str
    symbol: str      # generic format, e.g. "BTC/USDT"
    base_asset: str  # e.g. "BTC"
    quote_asset: str # e.g. "USDT"
    volume_24h: Decimal | None
    price: Decimal | None
```

### 4.3 Adapter Method

Add to `ExchangeAdapter` (base class):

```python
@abc.abstractmethod
async def fetch_all_symbols(self) -> list[SymbolInfo]:
    """Fetch all tradeable symbols from this exchange."""
    raise NotImplementedError
```

**Implementation notes per exchange (confirmed by live API testing):**

#### Binance (✅ Live tested)
- **Endpoint**: `GET https://api.binance.com/api/v3/exchangeInfo`
- **Internal format**: `BTCUSDT` (concatenated uppercase)
- **Ticker/depth accepts**: `BTCUSDT` only (`BTC/USDT` rejected)
- **Fields**: `symbol`, `status`, `baseAsset`, `baseAssetPrecision`, `quoteAsset`, `quotePrecision`, `quoteAssetPrecision`, `baseCommissionPrecision`, `quoteCommissionPrecision`, `orderTypes`, `icebergAllowed`, `ocoAllowed`, `otoAllowed`, `opoAllowed`, `quoteOrderQtyMarketAllowed`, `allowTrailingStop`, `cancelReplaceAllowed`, `amendAllowed`, `pegInstructionsAllowed`, `isSpotTradingAllowed`, `isMarginTradingAllowed`, `filters`, `permissions`, `permissionSets`, `defaultSelfTradePreventionMode`, `allowedSelfTradePreventionModes`
- **Volume key**: `quoteVolume`
- **Base volume key**: `volume`
- **Status key**: `status`, tradeable value: `"TRADING"`
- **Conversion**: `BTC/USDT` → `BTCUSDT` (remove `/`, uppercase)

#### Bitget (❌ Discovery failed, inferred from existing adapter)
- **Endpoint**: `GET https://api.bitget.com/api/v2/spot/public/symbols`
- **Internal format**: `BTCUSDT` (concatenated uppercase)
- **Volume key**: `vol24h`
- **Conversion**: `BTC/USDT` → `BTCUSDT` (remove `/`, uppercase)

#### HTX (❌ Discovery failed, inferred from existing adapter)
- **Endpoint**: `GET https://api.huobi.pro/v1/common/symbols`
- **Internal format**: `btcusdt` (concatenated lowercase)
- **Volume key**: `vol`
- **Conversion**: `BTC/USDT` → `btcusdt` (remove `/`, lowercase)

#### Hyperliquid (❌ Discovery failed, inferred from existing adapter)
- **Endpoint**: `POST https://api.hyperliquid.xyz/info` with body `{"type": "metaAndAssetCtxs"}`
- **Internal format**: `BTC` (coin name only, perpetuals with implicit USDC quote)
- **Volume key**: `dayNtlVlm`
- **Conversion**: `BTC/USDT` → `BTC` (remove `/USDT`, remove `/USDC`)
- **Reverse**: `BTC` → `BTC/USDC` (Hyperliquid is USDC-margined perpetuals)

### 4.4 Symbol Format Conversion Summary

| Exchange | Internal Format | Generic → Internal | Internal → Generic |
|----------|-----------------|-------------------|-------------------|
| Binance | `BTCUSDT` | `s.replace("/", "").upper()` | Match suffix `USDT`/`USDC`/`ETH`/`BTC`, insert `/` |
| Bitget | `BTCUSDT` | `s.replace("/", "").upper()` | Same as Binance |
| HTX | `btcusdt` | `s.replace("/", "").lower()` | Uppercase, match suffix, insert `/` |
| Hyperliquid | `BTC` | `s.replace("/", "").replace("USDT", "").replace("USDC", "")` | Append `/USDC` |

**Reverse conversion edge case**: To convert `BTCUSDT` back to generic, we match against known quote assets: `USDT`, `USDC`, `BUSD`, `ETH`, `BTC`. We try each suffix and split at the match.

---

## 5. Active Exchange Selection

The user explicitly chooses which **enabled** exchanges to use for symbol caching. This is separate from the exchange's `enabled` flag (which only means "has credentials").

### 5.1 API Endpoints

**List enabled exchanges:**
```http
GET /api/exchanges
```
Response:
```json
{
  "exchanges": [
    {"name": "binance", "enabled": true, "active": true},
    {"name": "bitget", "enabled": true, "active": false}
  ]
}
```

**Set active exchanges:**
```http
POST /api/exchanges/active
Content-Type: application/json

{"exchanges": ["binance", "bitget"]}
```

**Behavior:**
- Validates all names are registered and enabled.
- Stores active list in app state.
- Next cache refresh will only fetch from active exchanges.
- Returns list of active exchanges.

---

## 6. PairList Plugin Discovery

### 6.1 Discovery Mechanism

At startup, the system scans `horizon/pairlists/*.py`, imports each file, and registers any `PairList` subclass found.

```python
class PairListRegistry:
    def __init__(self):
        self._pairlists: dict[str, type[PairList]] = {}

    def discover(self, package_dir: str) -> None:
        """Scan package_dir/*.py and register PairList subclasses."""
        ...

    def list_all(self) -> list[str]:
        return sorted(self._pairlists.keys())

    def create(self, name: str) -> PairList:
        cls = self._pairlists[name]
        return cls()
```

### 6.2 Built-in PairLists

#### `top_coins.py`
Returns the 50 symbols with highest 24h volume across all cached exchanges.

```python
class TopCoinsPairList(PairList):
    @property
    def name(self) -> str: return "top_coins"
    @property
    def description(self) -> str: return "Top 50 coins by 24h volume"

    async def get_pairs(self, db: aiosqlite.Connection) -> list[str]:
        cursor = await db.execute(
            """
            SELECT symbol, MAX(CAST(volume_24h AS REAL)) AS max_vol
            FROM exchange_symbols
            WHERE volume_24h IS NOT NULL
            GROUP BY symbol
            ORDER BY max_vol DESC
            LIMIT 50
            """
        )
        return [row[0] for row in await cursor.fetchall()]
```

#### `usdt_only.py`
Returns all symbols quoted in USDT.

```python
class UsdtOnlyPairList(PairList):
    @property
    def name(self) -> str: return "usdt_only"
    @property
    def description(self) -> str: return "All symbols quoted in USDT"

    async def get_pairs(self, db: aiosqlite.Connection) -> list[str]:
        cursor = await db.execute(
            "SELECT DISTINCT symbol FROM exchange_symbols WHERE quote_asset = 'USDT' ORDER BY symbol"
        )
        return [row[0] for row in await cursor.fetchall()]
```

#### `all_symbols.py`
Returns all cached symbols, no filtering.

```python
class AllSymbolsPairList(PairList):
    @property
    def name(self) -> str: return "all_symbols"
    @property
    def description(self) -> str: return "All cached symbols from active exchanges"

    async def get_pairs(self, db: aiosqlite.Connection) -> list[str]:
        cursor = await db.execute(
            "SELECT DISTINCT symbol FROM exchange_symbols ORDER BY symbol"
        )
        return [row[0] for row in await cursor.fetchall()]
```

---

## 7. PairList API Endpoints

### 7.1 List Available PairLists

```http
GET /api/pairlists
```

Response:
```json
{
  "pairlists": [
    {"name": "top_coins", "description": "Top 50 coins by 24h volume"},
    {"name": "usdt_only", "description": "All symbols quoted in USDT"},
    {"name": "all_symbols", "description": "All cached symbols from active exchanges"}
  ]
}
```

### 7.2 Get Active PairList

```http
GET /api/pairlists/active
```

Response:
```json
{
  "name": "top_coins",
  "symbol_count": 50,
  "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
}
```

### 7.3 Set Active PairList

```http
POST /api/pairlists/active
Content-Type: application/json

{"name": "usdt_only"}
```

Response:
```json
{
  "name": "usdt_only",
  "symbol_count": 147,
  "symbols": ["BTC/USDT", "ETH/USDT", ...]
}
```

**Behavior:**

- Validates that the named PairList exists.
- Instantiates it, calls `get_pairs()`, stores the result.
- Updates `MarketDataFetcher.symbols` in real-time.
- Returns the symbol list and count.

### 7.4 Force Refresh Symbol Cache

```http
POST /api/pairlists/refresh-cache
```

Response:
```json
{
  "status": "started",
  "message": "Symbol cache refresh initiated for active exchanges: [binance, bitget]"
}
```

---

## 8. Integration with MarketDataFetcher

### 8.1 Current Behavior

`MarketDataFetcher` receives `symbols: list[str]` at construction and polls those forever.

### 8.2 New Behavior

- `MarketDataFetcher` holds a **reference** to the active PairList and DB connection.
- Every poll cycle, before fetching data, it calls `await active_pairlist.get_pairs(db)`.
- If the returned list differs from the previous cycle, it updates its internal symbol list seamlessly.
- No restart of the fetcher loop is required.

```python
class MarketDataFetcher:
    def __init__(self, registry, active_pairlist, db, poll_interval=10):
        self._registry = registry
        self._active_pairlist = active_pairlist
        self._db = db
        self._symbols: list[str] = []
        # ...

    async def _poll_loop(self):
        while not self._stop_event.is_set():
            # Refresh symbol list from PairList
            new_symbols = await self._active_pairlist.get_pairs(self._db)
            if set(new_symbols) != set(self._symbols):
                logger.info("PairList symbol list changed: %d -> %d symbols", len(self._symbols), len(new_symbols))
                self._symbols = new_symbols

            for symbol in self._symbols:
                ...
            await asyncio.sleep(self._poll_interval)
```

---

## 9. Background Tasks

### 9.1 Symbol Cache Refresh

A new background coroutine in `main.py` lifespan:

```python
async def _refresh_symbol_cache(active_exchanges: list[str], registry: ExchangeRegistry, db: aiosqlite.Connection):
    """Refresh the exchange_symbols cache from all active exchanges."""
    for exchange_name in active_exchanges:
        adapter = registry.get(exchange_name)
        if not adapter:
            continue
        try:
            symbols = await adapter.fetch_all_symbols()
            # Delete old entries for this exchange
            await db.execute("DELETE FROM exchange_symbols WHERE exchange = ?", (adapter.name,))
            # Insert new entries
            for sym in symbols:
                await db.execute(
                    """INSERT INTO exchange_symbols
                       (exchange, symbol, base_asset, quote_asset, volume_24h, price, last_updated)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (sym.exchange, sym.symbol, sym.base_asset, sym.quote_asset,
                     str(sym.volume_24h) if sym.volume_24h else None,
                     str(sym.price) if sym.price else None,
                     int(time.time() * 1000))
                )
            await db.commit()
            logger.info("Refreshed %d symbols from %s", len(symbols), adapter.name)
        except Exception as e:
            logger.warning("Failed to refresh symbols from %s: %s", adapter.name, e)
```

Triggered by:
- APScheduler job running every 24 hours
- Manual trigger via `POST /api/pairlists/refresh-cache`

---

## 10. Error Handling

| Scenario | Behavior |
|----------|----------|
| Exchange API fails during cache refresh | Log warning, keep stale cache entries for that exchange |
| `fetch_all_symbols()` returns empty | Clear that exchange's cache entries |
| Active PairList `get_pairs()` returns empty | MarketDataFetcher logs warning and skips cycle |
| User selects non-existent PairList | API returns 404 |
| PairList file has syntax error on discovery | Log error, skip that file |
| User selects inactive exchange | API returns 400 with "exchange not enabled" message |
| Cache is empty when PairList queries | PairList returns empty list; MarketDataFetcher logs warning |

---

## 11. Testing Strategy

- **Unit test**: Mock `aiosqlite.Connection`, verify `TopCoinsPairList.get_pairs()` returns correct symbols
- **Unit test**: Mock `ExchangeAdapter.fetch_all_symbols()`, verify cache refresh inserts correct rows
- **Integration test**: Start app, select exchanges, refresh cache, select PairList, verify `MarketDataFetcher._symbols` updates
- **Edge case**: PairList returns symbols not in cache → gracefully skipped
- **Edge case**: Reverse symbol conversion (`BTCUSDT` → `BTC/USDT`) with multiple quote asset suffixes

---

## 12. Files to Create / Modify

### New files
- `horizon/internal/pairlist/__init__.py`
- `horizon/internal/pairlist/base.py`
- `horizon/internal/pairlist/registry.py`
- `horizon/internal/pairlist/discovery.py`
- `horizon/pairlists/top_coins.py`
- `horizon/pairlists/usdt_only.py`
- `horizon/pairlists/all_symbols.py`
- `horizon/tests/test_pairlist.py`
- DB migration: `exchange_symbols` table

### Modified files
- `horizon/internal/exchange/adapter.py` — add `fetch_all_symbols()`
- `horizon/internal/exchange/binance.py` — implement `fetch_all_symbols()` (live tested)
- `horizon/internal/exchange/bitget.py` — implement `fetch_all_symbols()`
- `horizon/internal/exchange/htx.py` — implement `fetch_all_symbols()`
- `horizon/internal/exchange/hyperliquid.py` — implement `fetch_all_symbols()`
- `horizon/internal/marketdata/fetcher.py` — use PairList instead of static `symbols`
- `horizon/internal/web/server.py` — add exchange + PairList API endpoints
- `horizon/main.py` — wire PairListRegistry, active exchanges, cache refresh
- `horizon/internal/config/settings.py` — remove `market_data.symbols`

---

## Spec Self-Review Checklist

- [x] No TBD / TODO / placeholders
- [x] Internal consistency: cache table schema matches refresh logic and PairList queries
- [x] Scope: single focused feature (PairList + symbol cache + active exchange selection)
- [x] No ambiguity: `get_pairs()` returns generic-format symbols; cache stores generic format; adapters convert internally
- [x] Symbol format conversion documented with real API findings
- [x] Error handling covers main failure modes
- [x] Workflow matches user requirement: select exchange → cache → select PairList → dynamic coins → re-select → silent refresh

---

## Appendix A: Symbol Format Conventions

- **Generic format** (used throughout the system): `BTC/USDT` (slash-separated, uppercase)
- **Cache format**: generic format
- **Config/API format**: generic format

Each adapter's `fetch_all_symbols()` converts exchange-specific symbol strings to generic format before returning.

### Reverse Conversion Algorithm

To convert exchange-native format back to generic (e.g., `BTCUSDT` → `BTC/USDT`):

```python
KNOWN_QUOTES = ["USDT", "USDC", "BUSD", "ETH", "BTC", "DAI", "TUSD", "PAX"]

def to_generic(exchange_symbol: str) -> str:
    for quote in KNOWN_QUOTES:
        if exchange_symbol.endswith(quote):
            base = exchange_symbol[:-len(quote)]
            return f"{base}/{quote}"
    # Fallback: if no known quote matched, return as-is
    return exchange_symbol
```

For Hyperliquid: coin name always maps to `{coin}/USDC`.
