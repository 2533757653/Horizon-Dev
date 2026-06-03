"""Tests for paper trading symbol normalization.

Bug: PaperTradingSimulator stores positions using the exact string the
user submitted, so 'BTCUSDT' and 'BTC/USDT' (same underlying asset) end
up as TWO separate position rows. The fetcher/ticker lookup is
normalized, but the position insert is not.

Fix under test: all entry points (`simulate_market_order`,
`simulate_limit_order`, `close_paper_position`) must normalize the
symbol to a canonical form (`BASE/QUOTE`) BEFORE writing to
`paper_positions` so equivalent symbols collapse to one row.
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
import aiosqlite

from horizon.internal.exchange.types import Ticker
from horizon.internal.paper.simulator import PaperTradingSimulator


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db():
    """Create in-memory SQLite with the schema the simulator needs."""
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


def _make_fetcher():
    """Mock fetcher whose cache contains both 'BTC/USDT' and 'BTC/USDC' tickers."""
    fetcher = MagicMock()
    fetcher._tickers = {
        "BTC/USDT": {"binance": Ticker(
            symbol="BTC/USDT", price=Decimal("50000"),
            volume_24h=Decimal("1000"), exchange="binance", timestamp_ms=0,
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
    return PaperTradingSimulator(db=db, fetcher=_make_fetcher())


@pytest.mark.asyncio
async def test_market_order_normalizes_btcusdt_to_btc_usdt(simulator, db):
    """Symbol 'BTCUSDT' (no slash) must be stored as 'BTC/USDT' (with slash)."""
    await simulator.simulate_market_order(
        proposal_id=None,
        exchange="binance",
        symbol="BTCUSDT",
        side="buy",
        volume=Decimal("0.1"),
    )

    cursor = await db.execute(
        "SELECT symbol, volume FROM paper_positions WHERE exchange='binance'"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTC/USDT"
    assert rows[0]["volume"] == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_market_order_collapses_equivalent_symbols_into_one_position(simulator, db):
    """Submitting 'BTCUSDT' then 'BTC/USDT' must yield ONE position, not two."""
    await simulator.simulate_market_order(
        proposal_id=None, exchange="binance", symbol="BTCUSDT",
        side="buy", volume=Decimal("0.1"),
    )
    await simulator.simulate_market_order(
        proposal_id=None, exchange="binance", symbol="BTC/USDT",
        side="buy", volume=Decimal("0.05"),
    )

    cursor = await db.execute(
        "SELECT symbol, volume, side FROM paper_positions WHERE exchange='binance'"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1, f"Expected 1 position, got {len(rows)}: {rows}"
    assert rows[0]["symbol"] == "BTC/USDT"
    assert rows[0]["volume"] == pytest.approx(0.15)
    assert rows[0]["side"] == "buy"


@pytest.mark.asyncio
async def test_limit_order_normalizes_symbol(simulator, db):
    """Same normalization must apply to limit orders."""
    await simulator.simulate_limit_order(
        proposal_id=None, exchange="binance", symbol="BTCUSDT",
        side="buy", price=Decimal("51000"), volume=Decimal("0.1"),
    )

    cursor = await db.execute(
        "SELECT symbol FROM paper_positions WHERE exchange='binance'"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTC/USDT"
