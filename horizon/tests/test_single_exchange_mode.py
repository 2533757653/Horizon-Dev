"""Tests for single exchange mode functionality."""

import asyncio
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
    from horizon.internal.pairlist.base import PairList

    pairlist = MagicMock(spec=PairList)
    pairlist.name = "test"
    pairlist.get_pairs = AsyncMock(return_value=["BTC/USDT"])

    fetcher = MarketDataFetcher(
        registry=registry,
        db=MagicMock(),
        active_pairlist=pairlist,
        active_exchange="hyperliquid",
        poll_interval_seconds=10,
    )

    await fetcher.start()
    await asyncio.sleep(0.5)
    await fetcher.stop()

    assert "BTC/USDT" in fetcher._tickers
    assert "hyperliquid" in fetcher._tickers["BTC/USDT"]
    assert "binance" not in fetcher._tickers.get("BTC/USDT", {})
    assert "htx" not in fetcher._tickers.get("BTC/USDT", {})


@pytest.mark.asyncio
async def test_order_manager_validates_active_exchange(registry):
    """Test that OrderManager rejects orders to non-active exchanges."""
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    order_manager = OrderManager(
        registry=registry,
        db=db,
        active_exchange="binance",
    )

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


@pytest.mark.asyncio
async def test_order_manager_accepts_order_to_active_exchange(registry):
    """Test that OrderManager accepts orders to the active exchange."""
    db = MagicMock()
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