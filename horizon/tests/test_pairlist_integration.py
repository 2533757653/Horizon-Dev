"""Integration test: exchange selection → cache → PairList → MarketDataFetcher."""

import pytest
import pytest_asyncio
import asyncio
from unittest.mock import MagicMock, AsyncMock
import aiosqlite

from horizon.internal.pairlist.registry import PairListRegistry
from horizon.internal.pairlist.cache import SymbolCache
from horizon.internal.exchange.types import SymbolInfo
from decimal import Decimal


@pytest_asyncio.fixture
async def db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("""
        CREATE TABLE exchange_symbols (
            exchange TEXT NOT NULL, symbol TEXT NOT NULL,
            base_asset TEXT NOT NULL, quote_asset TEXT NOT NULL,
            volume_24h TEXT, price TEXT, last_updated INTEGER NOT NULL,
            PRIMARY KEY (exchange, symbol)
        )
    """)
    await conn.commit()
    yield conn
    await conn.close()


@pytest.fixture
def mock_adapter():
    adapter = MagicMock()
    adapter.name = "binance"
    adapter.fetch_all_symbols = AsyncMock(return_value=[
        SymbolInfo(exchange="binance", symbol="BTC/USDT", base_asset="BTC", quote_asset="USDT",
                   volume_24h=Decimal("1000000"), price=Decimal("50000")),
        SymbolInfo(exchange="binance", symbol="ETH/USDT", base_asset="ETH", quote_asset="USDT",
                   volume_24h=Decimal("500000"), price=Decimal("3000")),
        SymbolInfo(exchange="binance", symbol="SOL/USDT", base_asset="SOL", quote_asset="USDT",
                   volume_24h=Decimal("200000"), price=Decimal("100")),
    ])
    return adapter


@pytest.mark.asyncio
async def test_full_flow(db, mock_adapter):
    """Simulate: refresh cache → select PairList → verify symbols."""
    # Step 1: Refresh cache from mock adapter
    cache = SymbolCache(db)
    count = await cache.refresh_for_exchange(mock_adapter)
    assert count == 3

    # Step 2: Use top_coins PairList via registry
    registry = PairListRegistry()
    registry.discover("horizon.pairlists")

    # Test top_coins (should return by volume)
    top_pl = registry.create("top_coins")
    top_pairs = await top_pl.get_pairs(db)
    assert top_pairs[0] == "BTC/USDT"  # highest volume
    assert len(top_pairs) == 3

    # Test usdt_only
    usdt_pl = registry.create("usdt_only")
    usdt_pairs = await usdt_pl.get_pairs(db)
    assert len(usdt_pairs) == 3
    assert all("USDT" in p for p in usdt_pairs)

    # Test all_symbols
    all_pl = registry.create("all_symbols")
    all_pairs = await all_pl.get_pairs(db)
    assert len(all_pairs) == 3


@pytest.mark.asyncio
async def test_registry_discovery(db, mock_adapter):
    """Test that registry discovers all 3 built-in PairLists."""
    registry = PairListRegistry()
    registry.discover("horizon.pairlists")

    all_pls = registry.list_all()
    names = [p["name"] for p in all_pls]

    assert "top_coins" in names
    assert "usdt_only" in names
    assert "all_symbols" in names

    # Verify exists and create work
    assert registry.exists("top_coins")
    assert not registry.exists("nonexistent")

    pl = registry.create("top_coins")
    assert pl.name == "top_coins"


@pytest.mark.asyncio
async def test_symbol_cache_refresh(db, mock_adapter):
    """Test SymbolCache correctly replaces old data."""
    cache = SymbolCache(db)

    # First refresh
    count1 = await cache.refresh_for_exchange(mock_adapter)
    assert count1 == 3

    # Verify data is in DB
    pairs = await cache.get_all_pairs()
    assert len(pairs) == 3

    # Second refresh (should replace)
    count2 = await cache.refresh_for_exchange(mock_adapter)
    assert count2 == 3  # same count
    pairs2 = await cache.get_all_pairs()
    assert len(pairs2) == 3  # still 3, not 6