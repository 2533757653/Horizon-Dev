"""Tests for /api/portfolio/equity-curve and /api/portfolio/allocation endpoints.

These endpoints power the standalone /portfolio page and the
24h P&L change indicator in the main balance panel.
"""

import asyncio
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from horizon.internal.exchange.registry import ExchangeRegistry
from horizon.internal.exchange.types import Ticker
from horizon.internal.ordermanager.manager import OrderManager
from horizon.internal.proposals import TradeProposal
from horizon.internal.proposals.models import ProposalStatus
from horizon.internal.proposals.queue import ProposalQueue


# ===== Fixtures shared with test_web_server.py =====


@pytest_asyncio.fixture
async def db():
    """Create in-memory SQLite database with paper schema."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row

    # Minimal paper schema for the new endpoints
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_cash (
            id INTEGER PRIMARY KEY,
            initial_cash REAL NOT NULL,
            current_cash REAL NOT NULL
        )
    """)
    await conn.execute("""
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
        )
    """)
    await conn.execute("""
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
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_pnl (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            realized_pnl REAL DEFAULT 0.0,
            paper_pnl REAL DEFAULT 0.0,
            autonomous_trades INTEGER DEFAULT 0,
            manual_trades INTEGER DEFAULT 0,
            llm_proposals INTEGER DEFAULT 0,
            updated_at TIMESTAMP
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS strategy_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1,
            mode TEXT NOT NULL CHECK(mode IN ('paper','live')) DEFAULT 'live',
            autonomy_enabled INTEGER NOT NULL DEFAULT 0,
            min_confidence_threshold INTEGER DEFAULT 75,
            max_risk_tier TEXT DEFAULT 'low',
            analysis_interval_hours INTEGER DEFAULT 8,
            asset_whitelist TEXT NOT NULL,
            max_position_pct REAL DEFAULT 20.0,
            max_daily_loss_pct REAL DEFAULT 5.0,
            max_exchange_exposure_pct REAL DEFAULT 50.0,
            cooldown_seconds INTEGER DEFAULT 300,
            order_min_notional REAL DEFAULT 10.0,
            order_max_notional REAL DEFAULT 10000.0,
            price_drift_expiry_pct REAL DEFAULT 3.0,
            system_prompt TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS exchanges (
            name TEXT PRIMARY KEY,
            enabled INTEGER DEFAULT 1
        )
    """)
    await conn.execute("INSERT INTO exchanges (name, enabled) VALUES ('binance', 1)")
    await conn.execute("""
        INSERT INTO strategy_configs (
            name, enabled, mode, autonomy_enabled,
            min_confidence_threshold, max_risk_tier, analysis_interval_hours,
            asset_whitelist, max_position_pct, max_daily_loss_pct,
            max_exchange_exposure_pct, cooldown_seconds,
            order_min_notional, order_max_notional, price_drift_expiry_pct,
            system_prompt
        ) VALUES (
            'test_strategy', 1, 'paper', 0,
            75, 'low', 8,
            '["BTCUSDT","ETHUSDT"]', 20.0, 5.0,
            50.0, 300,
            10.0, 10000.0, 3.0,
            'You are a test trading analyst.'
        )
    """)

    await conn.commit()
    yield conn
    await conn.close()


@pytest.fixture
def order_manager():
    manager = MagicMock(spec=OrderManager)
    manager.submit_order = AsyncMock()
    return manager


@pytest.fixture
def strategy_config():
    return {"name": "test_strategy", "min_confidence_threshold": 75}


@pytest.fixture
def exchange_registry():
    return MagicMock(spec=ExchangeRegistry)


@pytest.fixture
def proposal_queue(db, order_manager, strategy_config, exchange_registry):
    queue = ProposalQueue(db, order_manager, strategy_config)
    queue.set_registry(exchange_registry)
    return queue


@pytest.fixture
def settings():
    s = MagicMock()
    s.app.host = "localhost"
    s.app.port = 8000
    s.app.log_level = "INFO"
    return s


@pytest.fixture
def fetcher():
    f = MagicMock()
    f.get_all_tickers = MagicMock(return_value=[])
    f.subscribe = MagicMock(return_value=asyncio.Queue())
    f.unsubscribe = MagicMock()
    return f


@pytest.fixture
def portfolio_tracker():
    t = MagicMock()
    snap = MagicMock()
    snap.exchanges = {}
    snap.total_usdt_value = Decimal("0")
    snap.timestamp_ms = 0
    t.get_snapshot = AsyncMock(return_value=snap)
    return t


@pytest.fixture
def paper_simulator():
    """Mock paper simulator for endpoint tests."""
    sim = MagicMock()
    sim.get_cash = AsyncMock(return_value={
        "initial_cash": 10000.0,
        "current_cash": 5000.0,
        "position_value": 5000.0,
        "total_balance": 10000.0,
    })
    sim.get_paper_positions = AsyncMock(return_value=[])
    return sim


@pytest_asyncio.fixture
async def client(db, settings, fetcher, order_manager, portfolio_tracker,
                 proposal_queue, exchange_registry, paper_simulator):
    from horizon.internal.web.server import create_app

    app = create_app(
        settings=settings,
        db=db,
        registry=exchange_registry,
        fetcher=fetcher,
        order_manager=order_manager,
        portfolio_tracker=portfolio_tracker,
        paper_simulator=paper_simulator,
    )
    with TestClient(app) as c:
        yield c


# ===== /api/portfolio/allocation tests =====


class TestPortfolioAllocationEmpty:
    def test_allocation_returns_total_value_field(self, client):
        response = client.get("/api/portfolio/allocation")
        assert response.status_code == 200
        data = response.json()
        assert "total_value" in data
        assert "allocations" in data

    def test_allocation_empty_paper_returns_cash_only(self, client):
        """No positions → only the cash (USDT) line, at 100%."""
        response = client.get("/api/portfolio/allocation")
        data = response.json()
        allocs = data["allocations"]
        assert len(allocs) == 1
        assert allocs[0]["asset"] == "USDT"
        assert allocs[0]["kind"] == "cash"
        assert allocs[0]["pct"] == 100.0


class TestPortfolioAllocationWithPositions:
    def test_allocation_includes_position_allocations(self, client, paper_simulator):
        """Two positions + cash → three allocation lines."""
        paper_simulator.get_paper_positions = AsyncMock(return_value=[
            {"exchange": "binance", "symbol": "BTCUSDT", "side": "buy",
             "volume": 0.1, "avg_entry_price": 50000.0, "current_price": 60000.0,
             "unrealized_pnl": 1000.0},
            {"exchange": "binance", "symbol": "ETHUSDT", "side": "buy",
             "volume": 5.0, "avg_entry_price": 3000.0, "current_price": 4000.0,
             "unrealized_pnl": 5000.0},
        ])
        response = client.get("/api/portfolio/allocation")
        data = response.json()
        allocs = {a["asset"]: a for a in data["allocations"]}

        assert "BTC" in allocs
        assert "ETH" in allocs
        assert "USDT" in allocs
        # BTC position value = 0.1 * 60000 = 6000
        assert allocs["BTC"]["value"] == 6000.0
        assert allocs["BTC"]["kind"] == "position"
        # ETH position value = 5 * 4000 = 20000
        assert allocs["ETH"]["value"] == 20000.0
        # Cash still 5000
        assert allocs["USDT"]["value"] == 5000.0

    def test_allocation_pcts_sum_to_100(self, client, paper_simulator):
        paper_simulator.get_paper_positions = AsyncMock(return_value=[
            {"exchange": "binance", "symbol": "BTCUSDT", "side": "buy",
             "volume": 0.1, "avg_entry_price": 50000.0, "current_price": 60000.0,
             "unrealized_pnl": 1000.0},
        ])
        response = client.get("/api/portfolio/allocation")
        data = response.json()
        total_pct = sum(a["pct"] for a in data["allocations"])
        # Allow tiny float error
        assert abs(total_pct - 100.0) < 0.01

    def test_allocation_normalizes_slash_symbols(self, client, paper_simulator):
        """Symbols with slashes (e.g. 'BTC/USDT') must be normalized to base asset only."""
        paper_simulator.get_paper_positions = AsyncMock(return_value=[
            {"exchange": "binance", "symbol": "BTC/USDT", "side": "buy",
             "volume": 0.1, "avg_entry_price": 50000.0, "current_price": 60000.0,
             "unrealized_pnl": 1000.0},
        ])
        response = client.get("/api/portfolio/allocation")
        data = response.json()
        assets = [a["asset"] for a in data["allocations"]]
        assert "BTC" in assets
        assert "BTC/" not in assets
        assert "BTC/USDT" not in assets


# ===== /api/portfolio/equity-curve tests =====


class TestEquityCurveEmpty:
    def test_equity_curve_returns_points_field(self, client):
        response = client.get("/api/portfolio/equity-curve")
        assert response.status_code == 200
        data = response.json()
        assert "points" in data
        assert "initial_cash" in data

    def test_equity_curve_empty_db_returns_only_initial(self, client):
        response = client.get("/api/portfolio/equity-curve")
        data = response.json()
        # With no daily_pnl rows, the only point is the initial_cash today
        assert data["initial_cash"] == 10000.0
        assert len(data["points"]) == 1
        assert data["points"][0]["value"] == 10000.0


class TestEquityCurveWithData:
    @pytest.mark.asyncio
    async def test_equity_curve_returns_cumulative_pnl(self, client, db):
        # Seed three days of daily_pnl
        await db.execute(
            "INSERT INTO daily_pnl (date, realized_pnl, paper_pnl) VALUES (?, ?, ?)",
            ("2026-01-01", 100.0, 100.0),
        )
        await db.execute(
            "INSERT INTO daily_pnl (date, realized_pnl, paper_pnl) VALUES (?, ?, ?)",
            ("2026-01-02", 50.0, 50.0),
        )
        await db.execute(
            "INSERT INTO daily_pnl (date, realized_pnl, paper_pnl) VALUES (?, ?, ?)",
            ("2026-01-03", -30.0, -30.0),
        )
        await db.commit()

        response = client.get("/api/portfolio/equity-curve")
        data = response.json()
        points = data["points"]
        # 3 daily_pnl + 1 initial = 4 points
        assert len(points) == 4
        # Cumulative: 10100, 10150, 10120
        assert points[0]["value"] == 10000.0
        assert points[1]["value"] == 10100.0
        assert points[2]["value"] == 10150.0
        assert points[3]["value"] == 10120.0

    @pytest.mark.asyncio
    async def test_equity_curve_orders_ascending_by_date(self, client, db):
        await db.execute(
            "INSERT INTO daily_pnl (date, realized_pnl, paper_pnl) VALUES (?, ?, ?)",
            ("2026-01-03", 30.0, 30.0),
        )
        await db.execute(
            "INSERT INTO daily_pnl (date, realized_pnl, paper_pnl) VALUES (?, ?, ?)",
            ("2026-01-01", 10.0, 10.0),
        )
        await db.execute(
            "INSERT INTO daily_pnl (date, realized_pnl, paper_pnl) VALUES (?, ?, ?)",
            ("2026-01-02", 20.0, 20.0),
        )
        await db.commit()

        response = client.get("/api/portfolio/equity-curve")
        data = response.json()
        dates = [p["date"] for p in data["points"]]
        # First point is always the "initial" cash baseline
        assert dates == ["initial", "2026-01-01", "2026-01-02", "2026-01-03"]


# ===== 24h P&L change test =====


class TestDailyChangeEndpoint:
    def test_daily_change_returns_24h_pnl(self, client):
        response = client.get("/api/portfolio/daily-change")
        assert response.status_code == 200
        data = response.json()
        assert "change_pct" in data
        assert "change_pnl" in data
        assert "previous_balance" in data
        assert "current_balance" in data

    def test_daily_change_with_no_history_returns_zero(self, client):
        response = client.get("/api/portfolio/daily-change")
        data = response.json()
        assert data["change_pct"] == 0.0
        assert data["change_pnl"] == 0.0


# ===== /api/paper/trades/stats endpoint =====


class TestPaperTradeStats:
    def test_trades_stats_endpoint_returns_stats(self, client):
        response = client.get("/api/paper/trades/stats")
        assert response.status_code == 200
        data = response.json()
        for key in ("total_trades", "closed_trades", "wins", "losses",
                    "win_rate_pct", "total_pnl", "realized_pnl",
                    "unrealized_pnl", "avg_pnl", "avg_holding_seconds"):
            assert key in data

    def test_trades_stats_empty_db_returns_zero(self, client):
        response = client.get("/api/paper/trades/stats")
        data = response.json()
        assert data["total_trades"] == 0
        assert data["win_rate_pct"] == 0.0

    @pytest.mark.asyncio
    async def test_trades_stats_with_closed_trades(self, client, db):
        await db.execute(
            """
            INSERT INTO paper_trades
            (id, exchange, symbol, side, order_type, price, volume,
             notional_value, status, filled_at, paper_pnl,
             closed_by_side, closed_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("t-1", "binance", "BTCUSDT", "buy", "market", 50000.0, 0.1,
             5000.0, "filled", "2026-01-01T00:00:00Z", 100.0,
             "sell", "2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.execute(
            """
            INSERT INTO paper_trades
            (id, exchange, symbol, side, order_type, price, volume,
             notional_value, status, filled_at, paper_pnl,
             closed_by_side, closed_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("t-2", "binance", "ETHUSDT", "buy", "market", 3000.0, 1.0,
             3000.0, "filled", "2026-01-01T00:00:00Z", -50.0,
             "sell", "2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await db.commit()

        response = client.get("/api/paper/trades/stats")
        data = response.json()
        assert data["total_trades"] == 2
        assert data["closed_trades"] == 2
        assert data["wins"] == 1
        assert data["losses"] == 1
        assert data["win_rate_pct"] == 50.0
        assert data["total_pnl"] == 50.0
        assert data["realized_pnl"] == 50.0
