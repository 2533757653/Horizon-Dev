# Single Exchange Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modify the system so that one instance interacts with only ONE exchange at a time, selected manually via config/API.

**Architecture:** Replace multi-exchange fan-out with a single `active_exchange` concept. All components (market data, portfolio, orders, symbol cache) route through the same exchange adapter. Exchange switching is manual via config or API call.

**Tech Stack:** Python asyncio, aiosqlite, FastAPI, aiohttp

---

## File Structure

```
horizon/
├── config.yaml                         # Add active_exchange field
├── main.py                              # Use active_exchange for symbol cache
├── internal/
│   ├── config/
│   │   └── settings.py                  # Add active_exchange to ExchangesSettings
│   ├── exchange/
│   │   └── registry.py                  # Add get_active_adapter() method
│   ├── marketdata/
│   │   └── fetcher.py                   # Use only active adapter
│   ├── portfolio/
│   │   └── tracker.py                   # Use only active adapter
│   ├── ordermanager/
│   │   └── manager.py                   # Validate against active exchange
│   └── pairlist/
│       └── cache.py                     # Refresh only from active exchange
│   └── web/
│       └── server.py                    # Update /api/exchanges/active endpoint
└── tests/
    └── test_single_exchange_mode.py    # New tests
```

---

## Task 1: Add `active_exchange` to Config and Settings

**Files:**
- Modify: `horizon/config.yaml`
- Modify: `horizon/internal/config/settings.py`

- [ ] **Step 1: Modify config.yaml**

Add `active_exchange: "hyperliquid"` under `exchanges:` section:

```yaml
exchanges:
  binance:
    enabled: true
    recv_window_ms: 5000
    api_key: "J4ihibjwgnIGHB3Rruc45yQM4Mgu8Bl7mpEpRVXxsmFbAxJJteFTna9SmVcWF6BK"
    api_secret: "XpHVRw7RZEOPxmjYkclYg3mCWZFJUVI6uLTXpW0zPuByGU5wxMAU52Mnu0KyI42i"
  htx:
    enabled: true
    recv_window_ms: 5000
    api_key: "61cb1905-ez2xc4vb6n-a4cd4bb3-beaf0"
    api_secret: "8644b004-a24dafb8-23e76d11-295ee"
  hyperliquid:
    enabled: true
    recv_window_ms: 5000
    api_key: ""
    api_secret: ""
    wallet_address: "0x8E373704658101D5BAEfAfCd24e23f56F9B17F1c"
    private_key: "e3f71933c40938f0f2df0b10ce888bff699144ac4fb0e9d50eef3fd1b8e5d4d5"
  bitget:
    enabled: false
    recv_window_ms: 5000
    api_key: "bg_745a999dfaf1dec65c9a249f48be003a"
    api_secret: "014ba3293f82c66f03153f43c846cbf2b0605bd70d51a7d1cdb7b36cee505e4e"
    passphrase: "TJh20040226"
  active_exchange: "hyperliquid"   # <-- ADD THIS LINE
```

- [ ] **Step 2: Modify settings.py**

In `ExchangesSettings` class, add `active_exchange` field:

```python
class ExchangesSettings(BaseSettings):
    """Container for all exchange configurations."""
    binance: BinanceConfig = Field(default_factory=BinanceConfig)
    htx: HTXConfig = Field(default_factory=HTXConfig)
    hyperliquid: HyperliquidConfig = Field(default_factory=HyperliquidConfig)
    bitget: BitgetConfig = Field(default_factory=BitgetConfig)
    active_exchange: str = Field(default="hyperliquid", description="The active exchange for all operations")
```

- [ ] **Step 3: Commit**

```bash
git add horizon/config.yaml horizon/internal/config/settings.py
git commit -m "feat: add active_exchange setting for single-exchange mode"
```

---

## Task 2: Add `get_active_adapter()` to ExchangeRegistry

**Files:**
- Modify: `horizon/internal/exchange/registry.py`

- [ ] **Step 1: Read the current file**

```python
# See current implementation at lines 1-72
```

- [ ] **Step 2: Add `get_active_adapter()` method**

Add this method to the `ExchangeRegistry` class:

```python
def get_active_adapter(self, active_exchange: str) -> Optional[ExchangeAdapter]:
    """Get the adapter for the active exchange.

    Args:
        active_exchange: Name of the active exchange.

    Returns:
        The exchange adapter if found and enabled, None otherwise.
    """
    adapter = self._adapters.get(active_exchange)
    if adapter is not None and adapter.enabled:
        return adapter
    return None
```

Add `Optional` to the imports at the top:
```python
from typing import Optional
```

- [ ] **Step 3: Commit**

```bash
git add horizon/internal/exchange/registry.py
git commit -m "feat: add get_active_adapter() method to ExchangeRegistry"
```

---

## Task 3: Modify MarketDataFetcher to Use Single Exchange

**Files:**
- Modify: `horizon/internal/marketdata/fetcher.py:95-152`

- [ ] **Step 1: Read the current `_fetch_symbol` method**

The current implementation calls all enabled adapters. We need to change it to use only the active adapter.

- [ ] **Step 2: Modify `_fetch_symbol` to use single adapter**

Replace the current `_fetch_symbol` method body (lines 95-152) with:

```python
async def _fetch_symbol(self, symbol: str) -> None:
    """Fetch ticker and orderbook for a symbol from the active exchange.

    Args:
        symbol: Trading pair symbol (e.g., 'BTC/USDT').
    """
    active_adapter = self._registry.get_active_adapter(self._active_exchange)
    if active_adapter is None:
        logger.warning(
            "No active adapter for exchange '%s' - skipping fetch for %s",
            self._active_exchange,
            symbol,
        )
        return

    async def fetch_with_error_handling(fetch_fn, *args, **kwargs):
        try:
            return await fetch_fn(*args, **kwargs)
        except Exception as e:
            logger.warning(
                "Failed to fetch %s from %s: %s",
                symbol,
                active_adapter.name,
                e,
            )
            return None

    # Fetch ticker
    ticker = await fetch_with_error_handling(
        active_adapter.fetch_ticker, symbol
    )

    if ticker is not None:
        async with self._lock:
            if symbol not in self._tickers:
                self._tickers[symbol] = {}
            self._tickers[symbol][active_adapter.name] = ticker

        await self._broadcast({
            "type": "ticker",
            "symbol": symbol,
            "exchange": active_adapter.name,
            "data": {
                "symbol": ticker.symbol,
                "price": str(ticker.price),
                "volume_24h": str(ticker.volume_24h),
                "exchange": ticker.exchange,
                "timestamp_ms": ticker.timestamp_ms,
            },
        })

    # Fetch orderbook
    orderbook = await fetch_with_error_handling(
        active_adapter.fetch_orderbook, symbol, 5
    )

    if orderbook is not None:
        async with self._lock:
            if symbol not in self._orderbooks:
                self._orderbooks[symbol] = {}
            self._orderbooks[symbol][active_adapter.name] = orderbook
```

- [ ] **Step 3: Update `__init__` to store `active_exchange`**

Add `active_exchange: str` parameter to `__init__`:

```python
def __init__(
    self,
    registry: ExchangeRegistry,
    db: aiosqlite.Connection,
    active_pairlist: "PairList",
    active_exchange: str,
    poll_interval_seconds: int = 10,
) -> None:
    """Initialize the market data fetcher.

    Args:
        registry: Exchange registry for accessing adapters.
        db: Database connection for PairList access.
        active_pairlist: PairList instance to get symbols from.
        active_exchange: Name of the active exchange to use.
        poll_interval_seconds: Interval between polling cycles.
    """
    self._registry = registry
    self._db = db
    self._active_pairlist = active_pairlist
    self._active_exchange = active_exchange
    # ... rest unchanged
```

- [ ] **Step 4: Commit**

```bash
git add horizon/internal/marketdata/fetcher.py
git commit -m "feat: modify MarketDataFetcher to use single active exchange"
```

---

## Task 4: Modify PortfolioTracker to Use Single Exchange

**Files:**
- Modify: `horizon/internal/portfolio/tracker.py:85-113`

- [ ] **Step 1: Read the current `_refresh_balances` method**

- [ ] **Step 2: Modify `_refresh_balances` to use single adapter**

Replace lines 85-113 with:

```python
async def _refresh_balances(self) -> None:
    """Refresh balances from the active exchange only."""
    active_adapter = self._registry.get_active_adapter(self._active_exchange)
    if active_adapter is None:
        logger.warning(
            "No active adapter for exchange '%s' - skipping balance fetch",
            self._active_exchange,
        )
        return

    try:
        balances = await active_adapter.fetch_all_balances()
        async with self._lock:
            self._balances[active_adapter.name] = {balance.asset: balance for balance in balances}
    except Exception as e:
        logger.warning(
            "Failed to fetch balances from %s: %s",
            active_adapter.name,
            e,
        )
```

- [ ] **Step 3: Update `__init__` to store `active_exchange`**

Add `active_exchange: str` parameter to `__init__`:

```python
def __init__(
    self,
    registry: ExchangeRegistry,
    db: aiosqlite.Connection,
    fetcher: Any,
    active_exchange: str,
    snapshot_interval_seconds: int = 60,
) -> None:
    """Initialize the portfolio tracker.

    Args:
        registry: Exchange registry for accessing adapters.
        db: Async SQLite database connection.
        fetcher: MarketDataFetcher instance for getting ticker prices.
        active_exchange: Name of the active exchange to use.
        snapshot_interval_seconds: Interval between snapshots in seconds.
    """
    self._registry = registry
    self._db = db
    self._fetcher = fetcher
    self._active_exchange = active_exchange
    self._snapshot_interval = snapshot_interval_seconds
    self._balances: dict[str, dict[str, Balance]] = {}
    self._snapshot_task: asyncio.Task | None = None
    self._stop_event: asyncio.Event | None = None
    self._lock = asyncio.Lock()
```

- [ ] **Step 4: Commit**

```bash
git add horizon/internal/portfolio/tracker.py
git commit -m "feat: modify PortfolioTracker to use single active exchange"
```

---

## Task 5: Modify SymbolCache to Use Single Exchange

**Files:**
- Modify: `horizon/internal/pairlist/cache.py`

- [ ] **Step 1: Add `refresh_for_exchange` method**

The current `refresh_for_exchange` already takes an adapter as parameter, so it naturally supports single exchange. But we need to add a method for refreshing from the active exchange only.

Add new method after `refresh_for_exchange`:

```python
async def refresh_active_exchange(self, adapter: "ExchangeAdapter") -> int:
    """Refresh symbol cache from the active exchange only.

    This replaces all symbols from other exchanges with only those from the active exchange.

    Args:
        adapter: The active exchange adapter to fetch from.

    Returns:
        Number of symbols inserted.
    """
    # First delete all symbols not from this exchange
    await self._db.execute(
        "DELETE FROM exchange_symbols WHERE exchange != ?",
        (adapter.name,),
    )
    await self._db.commit()

    # Then refresh from active exchange
    return await self.refresh_for_exchange(adapter)
```

- [ ] **Step 2: Commit**

```bash
git add horizon/internal/pairlist/cache.py
git commit -m "feat: add refresh_active_exchange method to SymbolCache"
```

---

## Task 6: Modify OrderManager to Validate Against Active Exchange

**Files:**
- Modify: `horizon/internal/ordermanager/manager.py:60-80`

- [ ] **Step 1: Update `submit_order` to validate against active exchange**

In the `submit_order` method, after validating that the adapter exists and is enabled (lines 73-79), add validation that the exchange matches the active exchange:

```python
# Validate exchange is the active exchange
if request.exchange != self._active_exchange:
    raise OrderSubmissionError(
        f"Exchange '{request.exchange}' is not the active exchange. "
        f"Current active exchange: '{self._active_exchange}'. "
        f"Submit orders to '{self._active_exchange}' only."
    )
```

Add this after line 79 (after the `if not adapter.enabled` check).

- [ ] **Step 2: Update `__init__` to store `active_exchange`**

Add `active_exchange: str` parameter to `__init__`:

```python
def __init__(self, registry: ExchangeRegistry, db: aiosqlite.Connection, active_exchange: str):
    """Initialize the OrderManager.

    Args:
        registry: Exchange registry for accessing exchange adapters.
        db: Async SQLite database connection.
        active_exchange: Name of the active exchange for order validation.
    """
    self._registry = registry
    self._db = db
    self._active_exchange = active_exchange
    self._open_orders: dict[str, OrderResult] = {}
    self._lock = asyncio.Lock()
    self._sync_task: asyncio.Task | None = None
    self._order_sync_interval_seconds: int = 30
```

- [ ] **Step 3: Update `_sync_open_orders` to use active exchange only**

Replace the `_sync_open_orders` method (lines 312-393) with:

```python
async def _sync_open_orders(self) -> None:
    """Sync open orders with exchange state for the active exchange only."""
    async with self._lock:
        active_adapter = self._registry.get_active_adapter(self._active_exchange)
        if active_adapter is None:
            return

        # Collect orders for active exchange only
        orders = [
            order for order in self._open_orders.values()
            if order.status in self.OPEN_STATUSES and order.exchange == self._active_exchange
        ]

        if not orders:
            return

        try:
            exchange_orders = await active_adapter.fetch_open_orders()
            exchange_order_map = {o.exchange_order_id: o for o in exchange_orders}

            for local_order in orders:
                exchange_order = exchange_order_map.get(local_order.exchange_order_id)

                if exchange_order is None:
                    if local_order.status in self.OPEN_STATUSES:
                        await self._db.execute(
                            "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
                            ("filled", self._get_current_timestamp_ms(), local_order.order_id),
                        )
                        await self._db.commit()
                        await self.record_event(
                            local_order.order_id,
                            "filled",
                            {"reason": "order_not_found_on_exchange"}
                        )
                        del self._open_orders[local_order.order_id]
                else:
                    if exchange_order.filled_volume > local_order.filled_volume:
                        event_type = "partial_fill" if exchange_order.filled_volume < exchange_order.volume else "filled"
                        await self.record_event(
                            local_order.order_id,
                            event_type,
                            {
                                "filled_volume": str(exchange_order.filled_volume),
                                "status": exchange_order.status,
                            }
                        )

                    self._open_orders[local_order.order_id] = exchange_order
                    await self._db.execute(
                        """
                        UPDATE orders
                        SET filled_volume = ?, status = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            float(exchange_order.filled_volume),
                            exchange_order.status,
                            self._get_current_timestamp_ms(),
                            local_order.order_id,
                        ),
                    )
                    await self._db.commit()

        except Exception:
            pass
```

- [ ] **Step 4: Commit**

```bash
git add horizon/internal/ordermanager/manager.py
git commit -m "feat: modify OrderManager to validate orders against active exchange"
```

---

## Task 7: Update main.py to Use Active Exchange

**Files:**
- Modify: `horizon/main.py:160-178`, `horizon/main.py:191-210`, `horizon/main.py:199-204`

- [ ] **Step 1: Update symbol cache refresh loop (lines 160-178)**

Replace the loop that refreshes all active exchanges with:

```python
# 2d. Refresh symbol cache from active exchange on startup
active_exchange = settings.exchanges.active_exchange
adapter = registry.get(active_exchange)
if adapter and adapter.enabled:
    try:
        await asyncio.wait_for(
            symbol_cache.refresh_active_exchange(adapter),
            timeout=10.0,
        )
        logger.info("Symbol cache refreshed from active exchange: %s", active_exchange)
    except asyncio.TimeoutError:
        logger.warning("Symbol cache refresh timed out for %s", active_exchange)
    except Exception as e:
        logger.warning("Symbol cache refresh failed for %s: %s", active_exchange, e)
else:
    logger.warning("Active exchange '%s' not found or not enabled", active_exchange)
logger.info("Symbol cache populated from active exchange: %s", active_exchange)
```

- [ ] **Step 2: Update MarketDataFetcher instantiation (lines 191-196)**

```python
fetcher = MarketDataFetcher(
    registry=registry,
    db=db,
    active_pairlist=active_pairlist,
    active_exchange=active_exchange,
    poll_interval_seconds=settings.trading.market_data_poll_interval_seconds,
)
```

- [ ] **Step 3: Update PortfolioTracker instantiation (lines 199-204)**

```python
portfolio_tracker = PortfolioTracker(
    registry=registry,
    db=db,
    fetcher=fetcher,
    active_exchange=active_exchange,
    snapshot_interval_seconds=settings.trading.portfolio_snapshot_interval_seconds,
)
```

- [ ] **Step 4: Update OrderManager instantiation (lines 207-210)**

```python
order_manager = OrderManager(
    registry=registry,
    db=db,
    active_exchange=active_exchange,
)
```

- [ ] **Step 5: Remove `active_exchanges` from app state**

In `main.py`, remove or update `application.state.active_exchanges = active_exchanges` since we're now using single exchange mode.

- [ ] **Step 6: Commit**

```bash
git add horizon/main.py
git commit -m "feat: update main.py to use active_exchange for single-exchange mode"
```

---

## Task 8: Update Web Server API Endpoints

**Files:**
- Modify: `horizon/internal/web/server.py`

- [ ] **Step 1: Update `/api/exchanges/active` endpoint**

Replace `POST /api/exchanges/active` (lines 146-163) to accept a single exchange name:

```python
class ActiveExchangeModel(BaseModel):
    """Request model for setting active exchange."""

    exchange: str = Field(..., description="Exchange name to set as active")

@app.post("/api/exchanges/active")
async def set_active_exchange(request: Request, body: ActiveExchangeModel) -> JSONResponse:
    """Set which exchange is active for all operations."""
    registry: ExchangeRegistry = request.app.state.registry
    enabled_names = {a.name for a in registry.list_enabled() if a.enabled}

    if body.exchange not in enabled_names:
        raise HTTPException(
            status_code=400,
            detail=f"Exchange '{body.exchange}' not enabled. Enabled: {sorted(enabled_names)}"
        )

    # Update settings and all components
    settings = request.app.state.settings
    settings.exchanges.active_exchange = body.exchange

    # Update fetcher
    fetcher = request.app.state.fetcher
    if hasattr(fetcher, '_active_exchange'):
        fetcher._active_exchange = body.exchange

    # Update portfolio tracker
    portfolio = request.app.state.portfolio_tracker
    if hasattr(portfolio, '_active_exchange'):
        portfolio._active_exchange = body.exchange

    # Update order manager
    order_mgr = request.app.state.order_manager
    if hasattr(order_mgr, '_active_exchange'):
        order_mgr._active_exchange = body.exchange

    return CustomJSONResponse(content={
        "active_exchange": body.exchange,
    })
```

- [ ] **Step 2: Update `/api/kline/{symbol}` endpoint (lines 282-294)**

```python
@app.get("/api/kline/{symbol}")
async def get_kline(
    symbol: str,
    timeframe: str = Query("1h", description="Timeframe: 1m, 5m, 15m, 1h, 4h, 1d"),
    limit: int = Query(500, description="Max candles to return"),
) -> JSONResponse:
    """Get historical OHLCV kline/candlestick data for a symbol."""
    registry: ExchangeRegistry = app.state.registry
    settings: Settings = app.state.settings
    active_exchange = settings.exchanges.active_exchange

    adapter = registry.get(active_exchange)
    if adapter is None or not adapter.enabled:
        raise HTTPException(status_code=404, detail=f"Active exchange '{active_exchange}' not available")

    try:
        candles = await adapter.fetch_klines(symbol, timeframe, limit)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"No kline data for {symbol}: {str(e)}")

    return CustomJSONResponse(content={
        "symbol": symbol,
        "timeframe": timeframe,
        "exchange": active_exchange,
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

- [ ] **Step 3: Update `/api/market-data/stream` to filter by active exchange**

In `market_data_stream`, the fetcher already filters by active exchange now.

- [ ] **Step 4: Commit**

```bash
git add horizon/internal/web/server.py
git commit -m "feat: update web server for single-exchange mode"
```

---

## Task 9: Write Tests for Single Exchange Mode

**Files:**
- Create: `horizon/tests/test_single_exchange_mode.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for single exchange mode functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal

from horizon.internal.exchange.registry import ExchangeRegistry
from horizon.internal.exchange.adapter import ExchangeAdapter
from horizon.internal.exchange.types import Ticker, Balance
from horizon.internal.marketdata.fetcher import MarketDataFetcher
from horizon.internal.portfolio.tracker import PortfolioTracker
from horizon.internal.ordermanager.manager import OrderManager, OrderSubmissionError


class MockAdapter(ExchangeAdapter):
    """Mock adapter for testing."""

    def __init__(self, name: str, enabled: bool = True):
        self._name = name
        self._enabled = enabled

    @property
    def name(self) -> str:
        return self._name

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def fetch_ticker(self, symbol: str) -> Ticker:
        return Ticker(
            symbol=symbol,
            price=Decimal("100.0"),
            volume_24h=Decimal("1000.0"),
            exchange=self._name,
            timestamp_ms=1234567890,
        )

    async def fetch_orderbook(self, symbol: str, depth: int = 5):
        from horizon.internal.exchange.types import OrderBook, OrderBookEntry
        return OrderBook(
            symbol=symbol,
            exchange=self._name,
            bids=[OrderBookEntry(price=Decimal("99.0"), size=Decimal("1.0"))],
            asks=[OrderBookEntry(price=Decimal("101.0"), size=Decimal("1.0"))],
        )

    async def fetch_all_balances(self) -> list[Balance]:
        return [
            Balance(asset="USDT", free=Decimal("1000.0"), locked=Decimal("0.0"), exchange=self._name),
        ]

    async def place_market_order(self, symbol: str, side: str, volume: Decimal):
        from horizon.internal.exchange.types import OrderResult
        return OrderResult(
            order_id="123",
            exchange_order_id="123",
            exchange=self._name,
            symbol=symbol,
            side=side,
            order_type="market",
            price=None,
            volume=volume,
            filled_volume=Decimal("0"),
            status="submitted",
            created_at_ms=1234567890,
            updated_at_ms=1234567890,
        )

    async def place_limit_order(self, symbol: str, side: str, price: Decimal, volume: Decimal):
        from horizon.internal.exchange.types import OrderResult
        return OrderResult(
            order_id="123",
            exchange_order_id="123",
            exchange=self._name,
            symbol=symbol,
            side=side,
            order_type="limit",
            price=price,
            volume=volume,
            filled_volume=Decimal("0"),
            status="submitted",
            created_at_ms=1234567890,
            updated_at_ms=1234567890,
        )

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> bool:
        return True

    async def fetch_open_orders(self, symbol: str = None):
        return []

    async def fetch_all_symbols(self):
        from horizon.internal.exchange.types import SymbolInfo
        return [
            SymbolInfo(exchange=self._name, symbol="BTC/USDT", base_asset="BTC", quote_asset="USDT", volume_24h=Decimal("1000.0"), price=Decimal("100.0")),
        ]


@pytest.fixture
def registry():
    """Create a registry with multiple adapters."""
    reg = ExchangeRegistry()
    reg.register(MockAdapter("binance"))
    reg.register(MockAdapter("htx"))
    reg.register(MockAdapter("hyperliquid"))
    return reg


def test_get_active_adapter_returns_enabled_adapter(registry):
    """Test that get_active_adapter returns the correct adapter."""
    adapter = registry.get_active_adapter("binance")
    assert adapter is not None
    assert adapter.name == "binance"


def test_get_active_adapter_returns_none_for_disabled(registry):
    """Test that get_active_adapter returns None for disabled adapters."""
    # Register a disabled adapter
    disabled_adapter = MockAdapter("disabled_exchange", enabled=False)
    registry.register(disabled_adapter)

    adapter = registry.get_active_adapter("disabled_exchange")
    assert adapter is None


def test_get_active_adapter_returns_none_for_missing(registry):
    """Test that get_active_adapter returns None for non-existent exchange."""
    adapter = registry.get_active_adapter("nonexistent")
    assert adapter is None


@pytest.mark.asyncio
async def test_fetcher_uses_only_active_exchange(registry):
    """Test that MarketDataFetcher only queries the active exchange."""
    from unittest.mock import AsyncMock, patch
    from horizon.internal.pairlist.base import PairList

    # Create mock pairlist
    pairlist = MagicMock(spec=PairList)
    pairlist.name = "test"
    pairlist.get_pairs = AsyncMock(return_value=["BTC/USDT"])

    # Create fetcher with hyperliquid as active
    fetcher = MarketDataFetcher(
        registry=registry,
        db=MagicMock(),
        active_pairlist=pairlist,
        active_exchange="hyperliquid",
        poll_interval_seconds=10,
    )

    # Start fetching
    await fetcher.start()

    # Wait a bit for the fetch to happen
    await asyncio.sleep(0.5)

    # Stop fetcher
    await fetcher.stop()

    # Verify that only hyperliquid ticker was fetched
    assert "BTC/USDT" in fetcher._tickers
    assert "hyperliquid" in fetcher._tickers["BTC/USDT"]
    # Binance and HTX should not have tickers
    assert "binance" not in fetcher._tickers.get("BTC/USDT", {})
    assert "htx" not in fetcher._tickers.get("BTC/USDT", {})


@pytest.mark.asyncio
async def test_order_manager_validates_active_exchange(registry):
    """Test that OrderManager rejects orders to non-active exchanges."""
    from horizon.internal.ordermanager.manager import OrderManager, OrderSubmissionError

    # Create order manager with binance as active
    order_manager = OrderManager(
        registry=registry,
        db=MagicMock(),
        active_exchange="binance",
    )

    # Try to submit order to htx (not active)
    request = OrderManager.OrderRequest(
        exchange="htx",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        price=None,
        volume=Decimal("0.1"),
        source="test",
    )

    with pytest.raises(OrderSubmissionError) as exc_info:
        await order_manager.submit_order(request)

    assert "not the active exchange" in str(exc_info.value)
    assert "htx" in str(exc_info.value)
    assert "binance" in str(exc_info.value)


@pytest.mark.asyncio
async def test_order_manager_accepts_order_to_active_exchange(registry):
    """Test that OrderManager accepts orders to the active exchange."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    order_manager = OrderManager(
        registry=registry,
        db=db,
        active_exchange="binance",
    )

    request = OrderManager.OrderRequest(
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        price=None,
        volume=Decimal("0.1"),
        source="test",
    )

    result = await order_manager.submit_order(request)

    assert result.exchange == "binance"
    assert result.symbol == "BTC/USDT"
```

- [ ] **Step 2: Run tests**

```bash
cd D:\Horizon-Dev && python -m pytest horizon/tests/test_single_exchange_mode.py -v
```

Expected: Tests should pass (or fail with proper assertion messages if implementation not complete)

- [ ] **Step 3: Commit**

```bash
git add horizon/tests/test_single_exchange_mode.py
git commit -m "test: add tests for single exchange mode"
```

---

## Task 10: Run Full Integration Test

**Files:**
- None (integration test)

- [ ] **Step 1: Start the server and verify**

```bash
cd D:\Horizon-Dev && python -m horizon.main
```

- [ ] **Step 2: Check logs for correct behavior**

Look for:
- `Symbol cache populated from active exchange: hyperliquid` (only ONE exchange)
- No more fan-out to all 3 exchanges
- Balance fetches only from hyperliquid

- [ ] **Step 3: Commit final state**

```bash
git add -a && git commit -m "feat: complete single-exchange mode implementation"
```

---

## Verification Checklist

After implementation, verify:

- [ ] Config has `active_exchange: "hyperliquid"`
- [ ] `GET /api/exchanges` shows one active exchange
- [ ] `POST /api/exchanges/active` switches the active exchange
- [ ] Market data fetches only from active exchange
- [ ] Portfolio balances only from active exchange
- [ ] Orders only submitted to active exchange
- [ ] Symbol cache refresh only from active exchange
- [ ] All tests pass

---

## Plan Complete

Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?