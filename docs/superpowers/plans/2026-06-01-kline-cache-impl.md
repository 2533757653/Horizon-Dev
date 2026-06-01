# Kline Cache with Lazy Loading - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a unified Kline cache using CryptoCompare as the data source with pickle persistence and lazy loading.

**Architecture:** Create a new `datasource` module with KlineCache (persistence + stale detection), CryptoCompareAdapter (API client), and DataSourceRegistry (fallback logic). Integrate with existing web server `/api/kline` endpoint.

**Tech Stack:** Python asyncio, pickle, aiohttp, CryptoCompare API

---

## File Structure

```
horizon/
├── internal/
│   └── datasource/
│       ├── __init__.py
│       ├── cache.py          # KlineCache class
│       ├── cryptocompare.py   # CryptoCompareAdapter
│       └── registry.py       # DataSourceRegistry
├── internal/
│   └── config/
│       └── settings.py      # Add datasource config
├── internal/
│   └── web/
│       └── server.py        # Modify /api/kline endpoint
└── tests/
    └── test_kline_cache.py  # Tests
```

---

## Task 1: Create KlineCache Class with Pickle Persistence

**Files:**
- Create: `horizon/internal/datasource/__init__.py`
- Create: `horizon/internal/datasource/cache.py`
- Test: `horizon/tests/test_kline_cache.py`

- [ ] **Step 1: Create datasource __init__.py**

```python
"""Data source module for market data."""

from .cache import KlineCache
from .cryptocompare import CryptoCompareAdapter
from .registry import DataSourceRegistry

__all__ = ["KlineCache", "CryptoCompareAdapter", "DataSourceRegistry"]
```

- [ ] **Step 2: Create KlineCache class**

```python
"""Kline cache with pickle persistence and lazy loading."""

import logging
import os
import pickle
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class KlineCache:
    """Manages in-memory Kline cache with pickle persistence.

    Data structure:
        _cache = {
            "BTC/USDT": {
                "1h": [(timestamp, open, high, low, close, volume), ...],
                "4h": [...],
            },
            "ETH/USDT": {...},
        }
        _timestamps = {
            "BTC/USDT": {"1h": last_fetch_time, "4h": last_fetch_time},
        }
    """

    DEFAULT_STALE_SECONDS = 3600  # 1 hour

    def __init__(
        self,
        cache_file: str = "./data/kline_cache.pkl",
        stale_seconds: int = DEFAULT_STALE_SECONDS,
    ):
        """Initialize KlineCache.

        Args:
            cache_file: Path to pickle file for persistence.
            stale_seconds: Time in seconds before cache is considered stale.
        """
        self._cache_file = Path(cache_file)
        self._stale_seconds = stale_seconds
        self._cache: dict[str, dict[str, list]] = {}
        self._timestamps: dict[str, dict[str, float]] = {}

    def _ensure_dir(self) -> None:
        """Create cache directory if not exists."""
        parent = self._cache_file.parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> None:
        """Load cache from pickle file."""
        if not self._cache_file.exists():
            logger.info("Kline cache file not found, starting fresh")
            return

        try:
            with open(self._cache_file, "rb") as f:
                data = pickle.load(f)
            self._cache = data.get("cache", {})
            self._timestamps = data.get("timestamps", {})
            logger.info(
                "Kline cache loaded from %s: %d symbols",
                self._cache_file,
                len(self._cache),
            )
        except Exception as e:
            logger.warning("Failed to load kline cache: %s", e)
            self._cache = {}
            self._timestamps = {}

    def save(self) -> None:
        """Save cache to pickle file."""
        self._ensure_dir()
        try:
            data = {
                "cache": self._cache,
                "timestamps": self._timestamps,
            }
            with open(self._cache_file, "wb") as f:
                pickle.dump(data, f)
            logger.debug("Kline cache saved to %s", self._cache_file)
        except Exception as e:
            logger.warning("Failed to save kline cache: %s", e)

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
    ) -> Optional[list]:
        """Get cached candles for symbol/timeframe.

        Args:
            symbol: Trading symbol (e.g., 'BTC/USDT').
            timeframe: Timeframe (e.g., '1h').

        Returns:
            List of candle dicts or None if not cached.
        """
        return self._cache.get(symbol, {}).get(timeframe)

    def is_stale(self, symbol: str, timeframe: str) -> bool:
        """Check if cache for symbol/timeframe is stale.

        Args:
            symbol: Trading symbol.
            timeframe: Timeframe.

        Returns:
            True if cache is stale or doesn't exist.
        """
        last_fetch = self._timestamps.get(symbol, {}).get(timeframe, 0)
        return (time.time() - last_fetch) > self._stale_seconds

    def update_candles(
        self,
        symbol: str,
        timeframe: str,
        candles: list,
    ) -> None:
        """Update cache with new candles.

        Args:
            symbol: Trading symbol.
            timeframe: Timeframe.
            candles: List of candle dicts.
        """
        if symbol not in self._cache:
            self._cache[symbol] = {}
        if symbol not in self._timestamps:
            self._timestamps[symbol] = {}

        self._cache[symbol][timeframe] = candles
        self._timestamps[symbol][timeframe] = time.time()
        self.save()

    def get_all_symbols(self) -> list[str]:
        """Get list of all cached symbols."""
        return list(self._cache.keys())
```

- [ ] **Step 3: Run test to verify KlineCache works**

Run: `python -c "from horizon.internal.datasource.cache import KlineCache; c = KlineCache(); c.load(); print('OK')"`

Expected: OK (or "Kline cache file not found, starting fresh" on first run)

- [ ] **Step 4: Commit**

```bash
git add horizon/internal/datasource/__init__.py horizon/internal/datasource/cache.py
git commit -m "feat: add KlineCache class with pickle persistence"
```

---

## Task 2: Create CryptoCompareAdapter

**Files:**
- Create: `horizon/internal/datasource/cryptocompare.py`

- [ ] **Step 1: Create CryptoCompareAdapter class**

```python
"""CryptoCompare API adapter for Kline data."""

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# CryptoCompare doesn't require API key for basic endpoints
DEFAULT_BASE_URL = "https://min-api.cryptocompare.com"


class CryptoCompareAdapter:
    """Fetches OHLCV klines from CryptoCompare API."""

    TIMEOUT_SECONDS = 30

    def __init__(self, api_key: str = "", base_url: str = DEFAULT_BASE_URL):
        """Initialize CryptoCompare adapter.

        Args:
            api_key: Optional API key for higher rate limits.
            base_url: CryptoCompare API base URL.
        """
        self._api_key = api_key
        self._base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS)
            )
        return self._session

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def fetch_klines(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 500,
    ) -> list[dict]:
        """Fetch OHLCV klines from CryptoCompare.

        Args:
            symbol: Trading symbol in format 'BTC/USDT'.
            timeframe: Timeframe: '1m', '5m', '15m', '1h', '4h', '1d'.
            limit: Maximum number of candles to return.

        Returns:
            List of candle dicts: [{time, open, high, low, close, volume}, ...]
        """
        # Convert symbol: 'BTC/USDT' -> 'BTCUSDT'
        sym = symbol.replace("/", "")

        # Map timeframe to CryptoCompare format
        timeframe_map = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "1h": "60",
            "4h": "240",
            "1d": "D",
        }
        interval = timeframe_map.get(timeframe, "60")

        url = f"{self._base_url}/data/v2/histoday"
        if timeframe != "1d":
            url = f"{self._base_url}/data/v2/histohour"
            if timeframe in ("1m", "5m", "15m"):
                url = f"{self._base_url}/data/v2/histominute"

        params = {
            "fsym": sym.replace("USDT", "").replace("USDC", ""),  # e.g., 'BTC'
            "tsym": "USDT",
            "limit": limit,
            "aggregate": interval[-1] if interval[-1].isdigit() else "1",
        }

        if self._api_key:
            params["api_key"] = self._api_key

        try:
            session = await self._get_session()
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()

            if data.get("Response") != "Success":
                raise Exception(f"CryptoCompare API error: {data.get('Message', 'Unknown')}")

            candles = []
            for k in data.get("Data", {}).get("Data", []):
                candles.append({
                    "time": k.get("time", 0),
                    "open": float(k.get("open", 0)),
                    "high": float(k.get("high", 0)),
                    "low": float(k.get("low", 0)),
                    "close": float(k.get("close", 0)),
                    "volume": float(k.get("volumefrom", 0)),
                })

            return candles

        except Exception as e:
            logger.warning("Failed to fetch klines from CryptoCompare for %s: %s", symbol, e)
            raise
```

- [ ] **Step 2: Verify syntax**

Run: `python -m py_compile horizon/internal/datasource/cryptocompare.py && echo "OK"`

- [ ] **Step 3: Commit**

```bash
git add horizon/internal/datasource/cryptocompare.py
git commit -m "feat: add CryptoCompare adapter for kline data"
```

---

## Task 3: Create DataSourceRegistry with Fallback

**Files:**
- Create: `horizon/internal/datasource/registry.py`

- [ ] **Step 1: Create DataSourceRegistry class**

```python
"""Registry for data source adapters with fallback support."""

import logging
from typing import Optional

from .cache import KlineCache
from .cryptocompare import CryptoCompareAdapter

logger = logging.getLogger(__name__)


class DataSourceRegistry:
    """Registry for kline data sources with fallback logic."""

    def __init__(
        self,
        cache: KlineCache,
        cryptocompare_adapter: CryptoCompareAdapter,
    ):
        """Initialize DataSourceRegistry.

        Args:
            cache: KlineCache instance for persistence.
            cryptocompare_adapter: CryptoCompareAdapter instance.
        """
        self._cache = cache
        self._cc_adapter = cryptocompare_adapter

    async def get_klines(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 500,
        force_refresh: bool = False,
    ) -> list[dict]:
        """Get klines for symbol/timeframe.

        Uses lazy loading:
        - Returns cached data if fresh
        - Fetches from CryptoCompare if stale or force_refresh

        Args:
            symbol: Trading symbol (e.g., 'BTC/USDT').
            timeframe: Timeframe (e.g., '1h').
            limit: Maximum candles to return.
            force_refresh: Force fetch from API even if cache is fresh.

        Returns:
            List of candle dicts.
        """
        # Try cache first (unless force_refresh)
        if not force_refresh:
            candles = self._cache.get_candles(symbol, timeframe)
            if candles is not None and not self._cache.is_stale(symbol, timeframe):
                logger.debug("Cache hit for %s %s", symbol, timeframe)
                return candles[-limit:]  # Return last N candles

        # Fetch from CryptoCompare
        try:
            candles = await self._cc_adapter.fetch_klines(symbol, timeframe, limit)
            self._cache.update_candles(symbol, timeframe, candles)
            return candles[-limit:]
        except Exception as e:
            logger.warning("CryptoCompare failed for %s: %s", symbol, e)

            # Fallback: try to return stale cache even if stale
            candles = self._cache.get_candles(symbol, timeframe)
            if candles is not None:
                logger.info("Returning stale cache for %s %s", symbol, timeframe)
                return candles[-limit:]

            # No cache available
            raise Exception(f"Failed to fetch klines for {symbol}: {e}")
```

- [ ] **Step 2: Verify syntax**

Run: `python -m py_compile horizon/internal/datasource/registry.py && echo "OK"`

- [ ] **Step 3: Commit**

```bash
git add horizon/internal/datasource/registry.py
git commit -m "feat: add DataSourceRegistry with fallback logic"
```

---

## Task 4: Add Datasource Config to Settings

**Files:**
- Modify: `horizon/internal/config/settings.py`
- Modify: `horizon/config.yaml`

- [ ] **Step 1: Add datasource config to settings.py**

Add after existing settings classes:

```python
class DataSourceSettings(BaseSettings):
    """Data source configuration."""
    cryptocompare_api_key: str = ""
    cryptocompare_base_url: str = "https://min-api.cryptocompare.com"
    cache_stale_seconds: int = 3600
    cache_file: str = "./data/kline_cache.pkl"
```

Add to main Settings class:
```python
datasource: DataSourceSettings = Field(default_factory=DataSourceSettings)
```

- [ ] **Step 2: Add to config.yaml**

```yaml
datasource:
  cryptocompare_api_key: ""  # Optional, leave empty for free tier
  cryptocompare_base_url: "https://min-api.cryptocompare.com"
  cache_stale_seconds: 3600  # 1 hour
  cache_file: "./data/kline_cache.pkl"
```

- [ ] **Step 3: Commit**

```bash
git add horizon/internal/config/settings.py horizon/config.yaml
git commit -m "feat: add datasource config for kline cache"
```

---

## Task 5: Integrate with Web Server

**Files:**
- Modify: `horizon/internal/web/server.py`

- [ ] **Step 1: Modify web server to use datasource**

Add imports:
```python
from ..datasource import DataSourceRegistry, KlineCache, CryptoCompareAdapter
```

Modify `create_app` function to initialize datasource:
```python
# Initialize kline cache and datasource
kline_cache = KlineCache(
    cache_file=settings.datasource.cache_file,
    stale_seconds=settings.datasource.cache_stale_seconds,
)
kline_cache.load()

cc_adapter = CryptoCompareAdapter(
    api_key=settings.datasource.cryptocompare_api_key,
    base_url=settings.datasource.cryptocompare_base_url,
)

datasource_registry = DataSourceRegistry(
    cache=kline_cache,
    cryptocompare_adapter=cc_adapter,
)

# Store in app state
app.state.kline_cache = kline_cache
app.state.datasource_registry = datasource_registry
```

- [ ] **Step 2: Modify GET /api/kline/{symbol} endpoint**

Replace the existing kline endpoint (lines ~297-333):

```python
@app.get("/api/kline/{symbol}")
async def get_kline(
    symbol: str,
    timeframe: str = Query("1h", description="Timeframe: 1m, 5m, 15m, 1h, 4h, 1d"),
    limit: int = Query(500, description="Max candles to return"),
    refresh: bool = Query(False, description="Force refresh from API"),
) -> JSONResponse:
    """Get historical OHLCV kline/candlestick data for a symbol."""
    registry: DataSourceRegistry = app.state.datasource_registry

    try:
        candles = await registry.get_klines(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            force_refresh=refresh,
        )
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"No kline data for {symbol}: {str(e)}")

    return CustomJSONResponse(content={
        "symbol": symbol,
        "timeframe": timeframe,
        "exchange": "cryptocompare",
        "candles": [
            {
                "time": c["time"],
                "open": str(c["open"]),
                "high": str(c["high"]),
                "low": str(c["low"]),
                "close": str(c["close"]),
                "volume": str(c["volume"]),
            }
            for c in candles
        ],
    })
```

- [ ] **Step 3: Verify syntax**

Run: `python -m py_compile horizon/internal/web/server.py && echo "OK"`

- [ ] **Step 4: Commit**

```bash
git add horizon/internal/web/server.py
git commit -m "feat: integrate kline cache with web server"
```

---

## Task 6: Write Tests for Kline Cache System

**Files:**
- Create: `horizon/tests/test_kline_cache.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for Kline cache system."""

import pytest
import time
from unittest.mock import AsyncMock, patch

from horizon.internal.datasource.cache import KlineCache
from horizon.internal.datasource.cryptocompare import CryptoCompareAdapter
from horizon.internal.datasource.registry import DataSourceRegistry


class TestKlineCache:
    """Tests for KlineCache class."""

    def test_cache_initialization(self):
        """Test KlineCache initializes with empty cache."""
        cache = KlineCache(cache_file="/tmp/test_cache.pkl")
        assert cache.get_candles("BTC/USDT", "1h") is None

    def test_cache_update_and_retrieve(self):
        """Test cache update and retrieval."""
        cache = KlineCache(cache_file="/tmp/test_cache.pkl")
        candles = [
            {"time": 1234567890, "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0, "volume": 1000.0},
        ]
        cache.update_candles("BTC/USDT", "1h", candles)
        result = cache.get_candles("BTC/USDT", "1h")
        assert result is not None
        assert len(result) == 1
        assert result[0]["close"] == 105.0

    def test_is_stale_after_update(self):
        """Test is_stale returns True for old cache."""
        cache = KlineCache(cache_file="/tmp/test_cache.pkl", stale_seconds=3600)
        cache.update_candles("BTC/USDT", "1h", [{"time": 1234567890}])
        # Immediately after update, should not be stale
        assert not cache.is_stale("BTC/USDT", "1h")

    def test_get_all_symbols(self):
        """Test get_all_symbols returns cached symbols."""
        cache = KlineCache(cache_file="/tmp/test_cache.pkl")
        cache.update_candles("BTC/USDT", "1h", [])
        cache.update_candles("ETH/USDT", "1h", [])
        symbols = cache.get_all_symbols()
        assert "BTC/USDT" in symbols
        assert "ETH/USDT" in symbols


class TestDataSourceRegistry:
    """Tests for DataSourceRegistry."""

    @pytest.mark.asyncio
    async def test_get_klines_uses_cache(self):
        """Test registry returns cached data when available."""
        cache = KlineCache(cache_file="/tmp/test_cache2.pkl")
        cache.update_candles("BTC/USDT", "1h", [
            {"time": 1234567890, "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0, "volume": 1000.0},
        ])

        cc_adapter = AsyncMock(spec=CryptoCompareAdapter)
        registry = DataSourceRegistry(cache=cache, cryptocompare_adapter=cc_adapter)

        # Should return cached data without calling adapter
        result = await registry.get_klines("BTC/USDT", "1h")
        assert len(result) == 1
        assert result[0]["close"] == 105.0
        cc_adapter.fetch_klines.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_klines_fetches_on_stale(self):
        """Test registry fetches from API when cache is stale."""
        cache = KlineCache(cache_file="/tmp/test_cache3.pkl", stale_seconds=0)
        cache.update_candles("BTC/USDT", "1h", [{"time": 1234567890, "close": 100.0}])

        cc_adapter = AsyncMock()
        cc_adapter.fetch_klines = AsyncMock(return_value=[
            {"time": 1234567891, "open": 101.0, "high": 111.0, "low": 91.0, "close": 106.0, "volume": 1001.0},
        ])

        registry = DataSourceRegistry(cache=cache, cryptocompare_adapter=cc_adapter)

        # Should fetch new data since stale_seconds=0
        result = await registry.get_klines("BTC/USDT", "1h", force_refresh=True)
        cc_adapter.fetch_klines.assert_called_once()
        assert result[0]["close"] == 106.0
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest horizon/tests/test_kline_cache.py -v`

- [ ] **Step 3: Commit**

```bash
git add horizon/tests/test_kline_cache.py
git commit -m "test: add tests for kline cache system"
```

---

## Task 7: Integration Test

**Files:**
- None (manual test)

- [ ] **Step 1: Start server and test endpoint**

```bash
cd D:/Horizon-Dev && python -m horizon.main
```

- [ ] **Step 2: Test kline endpoint**

```bash
curl "http://localhost:8080/api/kline/BTC-USDT?timeframe=1h&limit=10"
```

Expected: JSON response with candles from CryptoCompare (or from cache if loaded)

- [ ] **Step 3: Test refresh parameter**

```bash
curl "http://localhost:8080/api/kline/BTC-USDT?timeframe=1h&refresh=true"
```

Expected: Fresh data fetched from CryptoCompare, cache updated

- [ ] **Step 4: Check pickle file created**

```bash
ls -la data/kline_cache.pkl
```

Expected: File exists and was recently modified

- [ ] **Step 5: Commit integration test notes**

```bash
git add -a && git commit -m "feat: complete kline cache system with lazy loading"
```

---

## Verification Checklist

After implementation, verify:
- [ ] KlineCache saves/loads from pickle correctly
- [ ] CryptoCompareAdapter fetches valid kline data
- [ ] DataSourceRegistry uses cache when fresh
- [ ] `?refresh=true` forces API fetch
- [ ] `/api/kline/{symbol}` returns cached or fetched data
- [ ] Pickle file created in data/ directory
- [ ] All tests pass

---

## Plan Complete

Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?