"""Tests for paper trading cash-balance validation.

Bug: PaperTradingSimulator allows orders that drive `current_cash` below
zero without any check. There is no "liquidation" concept (this is paper
trading, not a real venue), so a simple cash floor is the correct
enforcement: a buy whose notional exceeds available cash must be
rejected with PaperTradeError.

Fix under test: `simulate_market_order` and `simulate_limit_order` must
verify that `current_cash + delta >= 0` (where delta = -notional for
buys) BEFORE writing to paper_trades / paper_positions / paper_cash.
"""

import asyncio
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
import aiosqlite

from horizon.internal.exchange.types import Ticker
from horizon.internal.paper.simulator import (
    PaperTradingSimulator,
    PaperTradeError,
)


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id TEXT PRIMARY KEY,
            proposal_id TEXT,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
            order_type TEXT NOT NULL CHECK (order_type IN ('market', 'limit')),
            price REAL NOT NULL,
            volume REAL NOT NULL,
            notional_value REAL NOT NULL,
            status TEXT CHECK (status IN ('pending', 'filled', 'expired', 'cancelled')),
            filled_at TIMESTAMP,
            paper_pnl REAL DEFAULT 0.0,
            closed_by_side TEXT,
            closed_at TIMESTAMP,
            created_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS paper_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
            volume REAL NOT NULL,
            avg_entry_price REAL NOT NULL,
            current_price REAL NOT NULL,
            unrealized_pnl REAL NOT NULL,
            opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(exchange, symbol, side)
        );
        CREATE TABLE IF NOT EXISTS daily_pnl (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            realized_pnl REAL DEFAULT 0.0,
            paper_pnl REAL DEFAULT 0.0,
            autonomous_trades INTEGER DEFAULT 0,
            manual_trades INTEGER DEFAULT 0,
            llm_proposals INTEGER DEFAULT 0,
            updated_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS paper_cash (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            initial_cash REAL NOT NULL,
            current_cash REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    await conn.commit()
    yield conn
    await conn.close()


def _make_fetcher(price=Decimal("50000")):
    fetcher = MagicMock()
    fetcher._tickers = {
        "BTC/USDT": {"binance": Ticker(
            symbol="BTC/USDT", price=price, volume_24h=Decimal("1000"),
            exchange="binance", timestamp_ms=0,
        )},
    }
    fetcher._registry = MagicMock()
    fetcher._registry.get_active_adapter.return_value = None
    fetcher.get_ticker.side_effect = lambda sym, ex=None: fetcher._tickers.get(sym, {}).get(ex) if ex else (
        next(iter(fetcher._tickers.get(sym, {}).values()), None)
    )
    return fetcher


@pytest_asyncio.fixture
async def simulator(db):
    sim = PaperTradingSimulator(db=db, fetcher=_make_fetcher(), initial_cash_usdt=10000.0)
    await sim.init_cash()
    return sim


@pytest.mark.asyncio
async def test_market_buy_with_sufficient_cash_succeeds(simulator, db):
    """Sanity: $10k cash, $5k order should succeed."""
    result = await simulator.simulate_market_order(
        proposal_id=None, exchange="binance", symbol="BTC/USDT",
        side="buy", volume=Decimal("0.1"),  # notional = 0.1 * 50000 = 5000
    )
    assert result.status == "filled"

    cursor = await db.execute("SELECT current_cash FROM paper_cash WHERE id=1")
    row = await cursor.fetchone()
    assert row["current_cash"] == pytest.approx(5000.0)


@pytest.mark.asyncio
async def test_market_buy_exceeding_cash_is_rejected(simulator, db):
    """$10k cash, $15k order must be rejected with PaperTradeError."""
    with pytest.raises(PaperTradeError, match="[Ii]nsufficient|[Cc]ash|exceed"):
        await simulator.simulate_market_order(
            proposal_id=None, exchange="binance", symbol="BTC/USDT",
            side="buy", volume=Decimal("0.3"),  # notional = 15000 > 10000
        )

    # CRITICAL: rejected order must not create a position
    cursor = await db.execute("SELECT COUNT(*) as c FROM paper_positions")
    row = await cursor.fetchone()
    assert row["c"] == 0, "Rejected order must not create a position"

    # CRITICAL: rejected order must not write a trade
    cursor = await db.execute("SELECT COUNT(*) as c FROM paper_trades")
    row = await cursor.fetchone()
    assert row["c"] == 0, "Rejected order must not write a trade"

    # CRITICAL: cash must be unchanged
    cursor = await db.execute("SELECT current_cash FROM paper_cash WHERE id=1")
    row = await cursor.fetchone()
    assert row["current_cash"] == pytest.approx(10000.0)


@pytest.mark.asyncio
async def test_limit_buy_exceeding_cash_is_rejected(simulator, db):
    """Limit order that would exceed cash must also be rejected."""
    with pytest.raises(PaperTradeError, match="[Ii]nsufficient|[Cc]ash|exceed"):
        await simulator.simulate_limit_order(
            proposal_id=None, exchange="binance", symbol="BTC/USDT",
            side="buy", price=Decimal("50000"),
            volume=Decimal("0.3"),  # notional = 15000 > 10000
        )

    cursor = await db.execute("SELECT COUNT(*) as c FROM paper_positions")
    row = await cursor.fetchone()
    assert row["c"] == 0


@pytest.mark.asyncio
async def test_sell_always_allowed_regardless_of_cash(simulator, db):
    """A sell can never reduce cash below zero (it ADDS cash), so it must
    always be permitted — even if current_cash is currently negative."""
    # Force cash into a deficit state to mirror the pre-fix production data
    await db.execute("UPDATE paper_cash SET current_cash = -1000.0 WHERE id=1")
    await db.commit()

    result = await simulator.simulate_market_order(
        proposal_id=None, exchange="binance", symbol="BTC/USDT",
        side="sell", volume=Decimal("0.01"),  # receives 500 USDT
    )
    assert result.status == "filled"

    cursor = await db.execute("SELECT current_cash FROM paper_cash WHERE id=1")
    row = await cursor.fetchone()
    # cash should improve by the sell notional
    assert row["current_cash"] == pytest.approx(-1000.0 + 500.0)


@pytest.mark.asyncio
async def test_second_buy_capped_at_remaining_cash(simulator, db):
    """After first buy, second buy is rejected if combined would exceed cash."""
    # First buy: spend 7000
    await simulator.simulate_market_order(
        proposal_id=None, exchange="binance", symbol="BTC/USDT",
        side="buy", volume=Decimal("0.14"),  # notional = 7000
    )
    # Second buy would need 5000 more → total 12000 > 10000 → reject
    with pytest.raises(PaperTradeError, match="[Ii]nsufficient|[Cc]ash|exceed"):
        await simulator.simulate_market_order(
            proposal_id=None, exchange="binance", symbol="BTC/USDT",
            side="buy", volume=Decimal("0.1"),  # notional = 5000
        )
