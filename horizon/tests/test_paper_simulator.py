"""Tests for paper trading simulator."""

import asyncio
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
import aiosqlite

from horizon.internal.exchange.types import Ticker
from horizon.internal.paper.simulator import (
    PaperTradingSimulator,
    PaperTradeResult,
    PaperTradeError,
)


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db():
    """Create in-memory SQLite database with paper trading tables."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row

    # Create required tables
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


@pytest.fixture
def mock_fetcher():
    """Create mock MarketDataFetcher."""
    fetcher = MagicMock()
    ticker = Ticker(
        symbol="BTC/USDT",
        price=Decimal("50000"),
        volume_24h=Decimal("1000"),
        exchange="binance",
        timestamp_ms=1234567890,
    )
    fetcher.get_ticker.return_value = ticker
    return fetcher


@pytest_asyncio.fixture
async def simulator(db, mock_fetcher):
    """Create PaperTradingSimulator instance."""
    return PaperTradingSimulator(db=db, fetcher=mock_fetcher)


@pytest.mark.asyncio
async def test_simulator_initialization(simulator, db, mock_fetcher):
    """Test simulator initializes correctly."""
    assert simulator._db is db
    assert simulator._fetcher is mock_fetcher


@pytest.mark.asyncio
async def test_simulate_market_order_buy(simulator, db):
    """Test market buy order simulation."""
    result = await simulator.simulate_market_order(
        proposal_id="prop-123",
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        volume=Decimal("0.1"),
    )

    assert result.order_id.startswith("PAPER-")
    assert result.exchange == "binance"
    assert result.symbol == "BTC/USDT"
    assert result.side == "buy"
    assert result.order_type == "market"
    assert result.price == Decimal("50000")
    assert result.volume == Decimal("0.1")
    assert result.notional_value == Decimal("5000")
    assert result.status == "filled"

    # Verify position was created
    cursor = await db.execute(
        "SELECT * FROM paper_positions WHERE exchange = ? AND symbol = ?",
        ("binance", "BTC/USDT"),
    )
    position = await cursor.fetchone()
    assert position is not None
    assert position["volume"] == 0.1
    assert position["side"] == "buy"


@pytest.mark.asyncio
async def test_simulate_market_order_sell(simulator, db):
    """Test market sell order simulation."""
    result = await simulator.simulate_market_order(
        proposal_id=None,
        exchange="binance",
        symbol="BTC/USDT",
        side="sell",
        volume=Decimal("0.05"),
    )

    assert result.status == "filled"
    assert result.price == Decimal("50000")


@pytest.mark.asyncio
async def test_simulate_market_order_no_ticker(db):
    """Test market order fails when no ticker available."""
    mock_fetcher = MagicMock()
    mock_fetcher.get_ticker.return_value = None
    sim = PaperTradingSimulator(db=db, fetcher=mock_fetcher)

    with pytest.raises(PaperTradeError, match="No ticker available"):
        await sim.simulate_market_order(
            proposal_id=None,
            exchange="binance",
            symbol="BTC/USDT",
            side="buy",
            volume=Decimal("0.1"),
        )


@pytest.mark.asyncio
async def test_simulate_limit_order_valid(simulator, db):
    """Test limit order within 2% range executes."""
    result = await simulator.simulate_limit_order(
        proposal_id="prop-456",
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        price=Decimal("51000"),  # Within 2% of 50000
        volume=Decimal("0.1"),
    )

    assert result.order_id.startswith("PAPER-")
    assert result.status == "filled"
    assert result.order_type == "limit"
    assert result.price == Decimal("51000")


@pytest.mark.asyncio
async def test_simulate_limit_order_too_high(simulator, db):
    """Test limit buy order exceeding 2% range fails."""
    with pytest.raises(PaperTradeError, match="exceeds 2% above market"):
        await simulator.simulate_limit_order(
            proposal_id="prop-789",
            exchange="binance",
            symbol="BTC/USDT",
            side="buy",
            price=Decimal("52000"),  # More than 2% above 50000
            volume=Decimal("0.1"),
        )


@pytest.mark.asyncio
async def test_simulate_limit_order_too_low(simulator, db):
    """Test limit sell order below 98% of market fails."""
    with pytest.raises(PaperTradeError, match="below 98%"):
        await simulator.simulate_limit_order(
            proposal_id="prop-999",
            exchange="binance",
            symbol="BTC/USDT",
            side="sell",
            price=Decimal("48000"),  # Less than 98% of 50000
            volume=Decimal("0.1"),
        )


@pytest.mark.asyncio
async def test_close_paper_position_full_close(simulator, db):
    """Test fully closing a position."""
    # First create a position
    await simulator.simulate_market_order(
        proposal_id="prop-1",
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        volume=Decimal("0.1"),
    )

    # Verify position exists
    cursor = await db.execute(
        "SELECT * FROM paper_positions WHERE exchange = ? AND symbol = ?",
        ("binance", "BTC/USDT"),
    )
    position = await cursor.fetchone()
    assert position is not None

    # Close the position (price dropped to 49000)
    mock_fetcher = MagicMock()
    mock_fetcher.get_ticker.return_value = Ticker(
        symbol="BTC/USDT",
        price=Decimal("49000"),  # Price dropped
        volume_24h=Decimal("1000"),
        exchange="binance",
        timestamp_ms=1234567890,
    )
    simulator._fetcher = mock_fetcher

    result = await simulator.close_paper_position(
        exchange="binance",
        symbol="BTC/USDT",
        side="sell",  # Close long with sell
        volume=None,  # Full close
    )

    assert result.status == "filled"
    assert result.paper_pnl == Decimal("-100")  # (49000 - 50000) * 0.1 = -100

    # Verify position is deleted
    cursor = await db.execute(
        "SELECT * FROM paper_positions WHERE exchange = ? AND symbol = ?",
        ("binance", "BTC/USDT"),
    )
    position = await cursor.fetchone()
    assert position is None


@pytest.mark.asyncio
async def test_close_paper_position_no_position(simulator, db):
    """Test closing non-existent position fails."""
    with pytest.raises(PaperTradeError, match="No buy position found"):
        await simulator.close_paper_position(
            exchange="binance",
            symbol="BTC/USDT",
            side="sell",
            volume=None,
        )


@pytest.mark.asyncio
async def test_update_market_prices(simulator, db):
    """Test updating market prices for all positions."""
    # Create a position
    await simulator.simulate_market_order(
        proposal_id="prop-1",
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        volume=Decimal("0.1"),
    )

    # Price increased to 51000
    mock_fetcher = MagicMock()
    mock_fetcher.get_ticker.return_value = Ticker(
        symbol="BTC/USDT",
        price=Decimal("51000"),
        volume_24h=Decimal("1000"),
        exchange="binance",
        timestamp_ms=1234567890,
    )
    simulator._fetcher = mock_fetcher

    await simulator.update_market_prices()

    # Verify unrealized P&L updated
    cursor = await db.execute(
        "SELECT unrealized_pnl FROM paper_positions WHERE exchange = ? AND symbol = ?",
        ("binance", "BTC/USDT"),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["unrealized_pnl"] == pytest.approx(100.0)  # (51000 - 50000) * 0.1


@pytest.mark.asyncio
async def test_get_paper_positions(simulator, db):
    """Test retrieving all paper positions."""
    # Create positions
    await simulator.simulate_market_order(
        proposal_id="prop-1",
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        volume=Decimal("0.1"),
    )
    await simulator.simulate_market_order(
        proposal_id="prop-2",
        exchange="binance",
        symbol="ETH/USDT",
        side="buy",
        volume=Decimal("1.0"),
    )

    positions = await simulator.get_paper_positions()
    assert len(positions) == 2
    symbols = {p["symbol"] for p in positions}
    assert "BTC/USDT" in symbols
    assert "ETH/USDT" in symbols


@pytest.mark.asyncio
async def test_get_paper_pnl_summary(simulator, db):
    """Test P&L summary calculation."""
    # Create a position and close it with profit
    await simulator.simulate_market_order(
        proposal_id="prop-1",
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        volume=Decimal("0.1"),
    )

    # Price increased to 51000 (profit)
    mock_fetcher = MagicMock()
    mock_fetcher.get_ticker.return_value = Ticker(
        symbol="BTC/USDT",
        price=Decimal("51000"),
        volume_24h=Decimal("1000"),
        exchange="binance",
        timestamp_ms=1234567890,
    )
    simulator._fetcher = mock_fetcher

    result = await simulator.close_paper_position(
        exchange="binance",
        symbol="BTC/USDT",
        side="sell",
        volume=None,
    )

    assert result.paper_pnl == Decimal("100")

    summary = await simulator.get_paper_pnl_summary()
    assert summary["total_realized_pnl"] == pytest.approx(100.0)
    assert summary["open_positions_count"] == 0
    assert summary["trades_count"] == 2  # Open + close


@pytest.mark.asyncio
async def test_paper_trade_result_dataclass():
    """Test PaperTradeResult dataclass."""
    result = PaperTradeResult(
        order_id="PAPER-123",
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        price=Decimal("50000"),
        volume=Decimal("0.1"),
        notional_value=Decimal("5000"),
        status="filled",
        paper_pnl=Decimal("0"),
    )

    assert result.order_id == "PAPER-123"
    assert result.notional_value == Decimal("5000")
    assert result.paper_pnl == Decimal("0")


def test_paper_trade_error_exception():
    """Test PaperTradeError is an Exception."""
    error = PaperTradeError("Test error")
    assert str(error) == "Test error"
    assert isinstance(error, Exception)