"""Tests for fast shutdown semantics.

These tests pin down the bug observed in production where Ctrl+C / SIGTERM
takes ~30 seconds to bring the server down. Root cause: background loops
use `_stop_event.set()` to signal exit, but the event is only checked at
the top of each iteration — long `asyncio.sleep()` calls in the loop body
mean `stop()` blocks waiting for the sleep to elapse.

Fix under test: `stop()` must `task.cancel()` the loop task so the sleep
is interrupted immediately. Acceptable shutdown latency: < 1 second for
both `MarketDataFetcher` and `PortfolioTracker`.
"""

import asyncio
import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from horizon.internal.exchange.registry import ExchangeRegistry
from horizon.internal.exchange.adapter import ExchangeAdapter
from horizon.internal.exchange.types import Ticker, Balance
from horizon.internal.marketdata.fetcher import MarketDataFetcher
from horizon.internal.portfolio.tracker import PortfolioTracker


class _MockAdapter(ExchangeAdapter):
    """Mock adapter that returns empty data quickly."""

    def __init__(self, name: str = "test_exchange", enabled: bool = True):
        self._name = name
        self._enabled = enabled

    @property
    def name(self) -> str:
        return self._name

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def fetch_ticker(self, symbol: str):
        return Ticker(
            symbol=symbol,
            price=Decimal("50000"),
            volume_24h=Decimal("1000"),
            exchange=self._name,
            timestamp_ms=0,
        )

    async def fetch_orderbook(self, symbol: str):
        return None

    async def fetch_all_symbols(self):
        return []

    async def fetch_balance(self, asset: str):
        return Balance(asset=asset, free=Decimal("0"), locked=Decimal("0"), exchange=self._name)

    async def fetch_all_balances(self):
        return []


@pytest.fixture
def registry():
    reg = ExchangeRegistry()
    reg.register(_MockAdapter("hyperliquid"))
    return reg


@pytest.mark.asyncio
async def test_fetcher_stop_returns_quickly_with_long_poll_interval(registry):
    """MarketDataFetcher.stop() must NOT block for the full poll_interval.

    With poll_interval=10s, stop() should return in well under 1s because
    the loop task should be cancelled mid-sleep.
    """
    pairlist = MagicMock()
    pairlist.name = "test"
    pairlist.get_pairs = AsyncMock(return_value=["BTC/USDT"])

    fetcher = MarketDataFetcher(
        registry=registry,
        db=MagicMock(),
        active_pairlist=pairlist,
        active_exchange="hyperliquid",
        poll_interval_seconds=10,  # long interval
    )

    await fetcher.start()
    # Give the loop a moment to enter the long sleep
    await asyncio.sleep(0.2)

    start = time.monotonic()
    await fetcher.stop()
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, (
        f"fetcher.stop() took {elapsed:.2f}s — should be < 1s. "
        "stop() is waiting for poll_interval sleep to elapse."
    )


@pytest.mark.asyncio
async def test_portfolio_tracker_stop_returns_quickly_with_long_snapshot_interval(registry):
    """PortfolioTracker.stop() must NOT block for the full snapshot_interval.

    With snapshot_interval=60s, stop() should return in well under 1s
    because the loop task should be cancelled mid-sleep.
    """
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    fetcher = MagicMock()
    fetcher.get_ticker = MagicMock(return_value=None)

    tracker = PortfolioTracker(
        registry=registry,
        db=db,
        fetcher=fetcher,
        active_exchange="hyperliquid",
        snapshot_interval_seconds=60,  # long interval
    )

    await tracker.start()
    # Give the loop a moment to enter the long sleep
    await asyncio.sleep(0.2)

    start = time.monotonic()
    await tracker.stop()
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, (
        f"tracker.stop() took {elapsed:.2f}s — should be < 1s. "
        "stop() is waiting for snapshot_interval sleep to elapse."
    )
