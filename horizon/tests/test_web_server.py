"""Tests for Web Server REST Endpoints."""

import asyncio
import json
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
from horizon.internal.exchange.types import Balance, Ticker
from horizon.internal.ordermanager.manager import OrderManager, OrderResult
from horizon.internal.proposals.models import ProposalStatus
from horizon.internal.proposals.queue import (
    InvalidProposalStateError,
    ProposalExpiredError,
    ProposalNotFoundError,
    ProposalQueue,
)
from horizon.internal.proposals import TradeProposal


class TestWebServerProposals:
    """Integration tests for proposal REST endpoints."""

    @pytest_asyncio.fixture
    async def db(self):
        """Create in-memory SQLite database with schema."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row

        # Create minimal schema
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                price REAL,
                volume REAL NOT NULL,
                confidence_score INTEGER NOT NULL,
                risk_tier TEXT NOT NULL,
                llm_rationale TEXT NOT NULL,
                llm_raw_response TEXT,
                technical_context TEXT,
                market_snapshot TEXT NOT NULL,
                portfolio_snapshot TEXT,
                guardrail_result TEXT,
                approved_by TEXT,
                approved_at TIMESTAMP,
                executed_order_id TEXT,
                expires_at TIMESTAMP NOT NULL,
                price_drift_threshold_pct REAL NOT NULL DEFAULT 3.0,
                proposed_price REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create orders table for FK constraint
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                price REAL,
                volume REAL NOT NULL,
                filled_volume REAL DEFAULT 0.0,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                proposal_id TEXT,
                exchange_order_id TEXT,
                created_at INTEGER,
                updated_at INTEGER
            )
        """)

        # Create exchanges table for FK constraint
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS exchanges (
                name TEXT PRIMARY KEY,
                enabled INTEGER DEFAULT 1
            )
        """)
        await conn.execute("INSERT INTO exchanges (name, enabled) VALUES ('binance', 1)")

        # Create llm_analysis_history table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_analysis_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_config_id INTEGER NOT NULL,
                triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completion_status TEXT NOT NULL,
                latency_ms INTEGER,
                token_count_input INTEGER,
                token_count_output INTEGER,
                raw_prompt TEXT NOT NULL,
                raw_response TEXT,
                parsed_proposals_count INTEGER DEFAULT 0,
                error_message TEXT
            )
        """)

        # Create strategy_configs table
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

        # Seed active strategy config
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
    def order_manager(self, db):
        """Create mock OrderManager."""
        manager = MagicMock(spec=OrderManager)
        manager.submit_order = AsyncMock()
        return manager

    @pytest.fixture
    def strategy_config(self):
        """Create strategy config."""
        return {
            "name": "test_strategy",
            "min_confidence_threshold": 75,
        }

    @pytest.fixture
    def exchange_registry(self):
        """Create mock exchange registry."""
        registry = MagicMock(spec=ExchangeRegistry)
        return registry

    @pytest.fixture
    def proposal_queue(self, db, order_manager, strategy_config, exchange_registry):
        """Create ProposalQueue instance."""
        queue = ProposalQueue(db, order_manager, strategy_config)
        queue.set_registry(exchange_registry)
        return queue

    @pytest.fixture
    def sample_proposal(self):
        """Create a sample TradeProposal."""
        return TradeProposal(
            id=str(uuid.uuid4()),
            status=ProposalStatus.PROPOSED.value,
            exchange="binance",
            symbol="BTCUSDT",
            side="buy",
            order_type="market",
            price=None,
            volume=0.1,
            confidence_score=85,
            risk_tier="low",
            llm_rationale="Test rationale",
            llm_raw_response="raw response",
            technical_context="{}",
            market_snapshot="{}",
            portfolio_snapshot=None,
            guardrail_result=None,
            approved_by=None,
            approved_at=None,
            executed_order_id=None,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
            price_drift_threshold_pct=3.0,
            proposed_price=95000.0,
            created_at=datetime.now(timezone.utc),
        )

    @pytest.fixture
    def settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.app.host = "localhost"
        settings.app.port = 8000
        settings.app.log_level = "INFO"
        return settings

    @pytest.fixture
    def fetcher(self):
        """Create mock MarketDataFetcher."""
        fetcher = MagicMock()
        fetcher.get_all_tickers = MagicMock(return_value=[])
        fetcher.subscribe = MagicMock(return_value=asyncio.Queue())
        fetcher.unsubscribe = MagicMock()
        return fetcher

    @pytest.fixture
    def portfolio_tracker(self):
        """Create mock PortfolioTracker."""
        tracker = MagicMock()
        # Create a mock snapshot object
        mock_snapshot = MagicMock()
        mock_snapshot.exchanges = {}
        mock_snapshot.total_usdt_value = Decimal("0")
        mock_snapshot.timestamp_ms = 0
        tracker.get_snapshot = AsyncMock(return_value=mock_snapshot)
        return tracker

    @pytest_asyncio.fixture
    async def client(self, db, settings, fetcher, order_manager, portfolio_tracker, proposal_queue, exchange_registry):
        """Create test client with web server."""
        from horizon.internal.web.server import create_app

        app = create_app(
            settings=settings,
            db=db,
            registry=exchange_registry,
            fetcher=fetcher,
            order_manager=order_manager,
            portfolio_tracker=portfolio_tracker,
            proposal_queue=proposal_queue,
        )

        # Use TestClient from fastapi
        with TestClient(app) as client:
            yield client

    # ===== GET /api/proposals Tests =====

    @pytest.mark.asyncio
    async def test_list_proposals_empty(self, client, proposal_queue, db):
        """Test GET /api/proposals returns empty list when no proposals."""
        response = client.get("/api/proposals")
        assert response.status_code == 200
        data = response.json()
        assert data["proposals"] == []
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_list_proposals_with_data(self, client, proposal_queue, sample_proposal, db):
        """Test GET /api/proposals returns proposals."""
        await proposal_queue.enqueue(sample_proposal)

        response = client.get("/api/proposals")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert len(data["proposals"]) == 1
        assert data["proposals"][0]["id"] == sample_proposal.id
        assert data["proposals"][0]["status"] == "proposed"

    @pytest.mark.asyncio
    async def test_list_proposals_filter_by_status(self, client, proposal_queue, sample_proposal, db):
        """Test GET /api/proposals?status=proposed filters correctly."""
        await proposal_queue.enqueue(sample_proposal)

        # Filter by proposed
        response = client.get("/api/proposals?status=proposed")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1

        # Filter by different status
        response = client.get("/api/proposals?status=approved")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_list_proposals_invalid_status(self, client, proposal_queue, db):
        """Test GET /api/proposals with invalid status returns 400."""
        response = client.get("/api/proposals?status=invalid")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_list_proposals_limit(self, client, proposal_queue, db):
        """Test GET /api/proposals respects limit parameter."""
        # Create multiple proposals
        for i in range(5):
            proposal = TradeProposal(
                id=str(uuid.uuid4()),
                status=ProposalStatus.PROPOSED.value,
                exchange="binance",
                symbol="BTCUSDT",
                side="buy",
                order_type="market",
                price=None,
                volume=0.1,
                confidence_score=85,
                risk_tier="low",
                llm_rationale="Test rationale",
                llm_raw_response="raw response",
                technical_context="{}",
                market_snapshot="{}",
                portfolio_snapshot=None,
                guardrail_result=None,
                approved_by=None,
                approved_at=None,
                executed_order_id=None,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
                price_drift_threshold_pct=3.0,
                proposed_price=95000.0,
                created_at=datetime.now(timezone.utc),
            )
            await proposal_queue.enqueue(proposal)

        response = client.get("/api/proposals?limit=3")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3

    # ===== GET /api/proposals/{proposal_id} Tests =====

    @pytest.mark.asyncio
    async def test_get_proposal(self, client, proposal_queue, sample_proposal, db):
        """Test GET /api/proposals/{id} returns single proposal."""
        await proposal_queue.enqueue(sample_proposal)

        response = client.get(f"/api/proposals/{sample_proposal.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == sample_proposal.id
        assert data["symbol"] == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_get_proposal_not_found(self, client, proposal_queue, db):
        """Test GET /api/proposals/{id} returns 404 for non-existent proposal."""
        response = client.get("/api/proposals/non-existent-id")
        assert response.status_code == 404

    # ===== POST /api/proposals/{proposal_id}/approve Tests =====

    @pytest.mark.asyncio
    async def test_approve_proposal_success(
        self, client, proposal_queue, sample_proposal, db, order_manager
    ):
        """Test POST /api/proposals/{id}/approve executes the proposal."""
        await proposal_queue.enqueue(sample_proposal)

        # Mock order submission
        order_result = OrderResult(
            order_id="order-123",
            exchange_order_id="exchange-order-123",
            exchange="binance",
            symbol="BTCUSDT",
            side="buy",
            order_type="market",
            price=None,
            volume=0.1,
            filled_volume=0.0,
            status="submitted",
            created_at_ms=0,
            updated_at_ms=0,
        )
        order_manager.submit_order.return_value = order_result

        # Mock price fetch
        mock_adapter = MagicMock()
        mock_adapter.enabled = True
        mock_adapter.fetch_ticker = AsyncMock(return_value=Ticker(
            symbol="BTCUSDT",
            price=Decimal("95000.0"),
            volume_24h=Decimal("1000.0"),
            exchange="binance",
            timestamp_ms=0,
        ))
        proposal_queue._registry.get.return_value = mock_adapter

        response = client.post(
            f"/api/proposals/{sample_proposal.id}/approve",
            json={"approved_by": "test_user"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "executed"
        assert data["approved_by"] == "test_user"

    @pytest.mark.asyncio
    async def test_approve_proposal_not_found(self, client, proposal_queue, db):
        """Test POST /api/proposals/{id}/approve returns 404 for non-existent proposal."""
        response = client.post(
            "/api/proposals/non-existent-id/approve",
            json={"approved_by": "test_user"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_proposal_already_approved(
        self, client, proposal_queue, sample_proposal, db, order_manager
    ):
        """Test POST /api/proposals/{id}/approve returns 400 for already-approved proposal."""
        await proposal_queue.enqueue(sample_proposal)

        # Mock order submission
        order_result = OrderResult(
            order_id="order-123",
            exchange_order_id="exchange-order-123",
            exchange="binance",
            symbol="BTCUSDT",
            side="buy",
            order_type="market",
            price=None,
            volume=0.1,
            filled_volume=0.0,
            status="submitted",
            created_at_ms=0,
            updated_at_ms=0,
        )
        order_manager.submit_order.return_value = order_result

        mock_adapter = MagicMock()
        mock_adapter.enabled = True
        mock_adapter.fetch_ticker = AsyncMock(return_value=Ticker(
            symbol="BTCUSDT",
            price=Decimal("95000.0"),
            volume_24h=Decimal("1000.0"),
            exchange="binance",
            timestamp_ms=0,
        ))
        proposal_queue._registry.get.return_value = mock_adapter

        # First approval
        client.post(
            f"/api/proposals/{sample_proposal.id}/approve",
            json={"approved_by": "test_user"},
        )

        # Try to approve again
        response = client.post(
            f"/api/proposals/{sample_proposal.id}/approve",
            json={"approved_by": "test_user"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_approve_proposal_expired(
        self, client, proposal_queue, sample_proposal, db, order_manager
    ):
        """Test POST /api/proposals/{id}/approve returns 410 for expired proposal."""
        # Set expires_at to past
        sample_proposal.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await proposal_queue.enqueue(sample_proposal)

        mock_adapter = MagicMock()
        mock_adapter.enabled = True
        mock_adapter.fetch_ticker = AsyncMock(return_value=Ticker(
            symbol="BTCUSDT",
            price=Decimal("95000.0"),
            volume_24h=Decimal("1000.0"),
            exchange="binance",
            timestamp_ms=0,
        ))
        proposal_queue._registry.get.return_value = mock_adapter

        response = client.post(
            f"/api/proposals/{sample_proposal.id}/approve",
            json={"approved_by": "test_user"},
        )
        assert response.status_code == 410

    # ===== POST /api/proposals/{proposal_id}/reject Tests =====

    @pytest.mark.asyncio
    async def test_reject_proposal_success(self, client, proposal_queue, sample_proposal, db):
        """Test POST /api/proposals/{id}/reject rejects the proposal."""
        await proposal_queue.enqueue(sample_proposal)

        response = client.post(
            f"/api/proposals/{sample_proposal.id}/reject",
            json={"rejected_by": "test_user", "reason": "Too risky"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "rejected"
        assert data["approved_by"] == "test_user"

    @pytest.mark.asyncio
    async def test_reject_proposal_not_found(self, client, proposal_queue, db):
        """Test POST /api/proposals/{id}/reject returns 404 for non-existent proposal."""
        response = client.post(
            "/api/proposals/non-existent-id/reject",
            json={"rejected_by": "test_user"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_reject_proposal_already_rejected(
        self, client, proposal_queue, sample_proposal, db, order_manager
    ):
        """Test POST /api/proposals/{id}/reject returns 400 for already-rejected proposal."""
        await proposal_queue.enqueue(sample_proposal)

        # Mock order submission for approval
        order_result = OrderResult(
            order_id="order-123",
            exchange_order_id="exchange-order-123",
            exchange="binance",
            symbol="BTCUSDT",
            side="buy",
            order_type="market",
            price=None,
            volume=0.1,
            filled_volume=0.0,
            status="submitted",
            created_at_ms=0,
            updated_at_ms=0,
        )
        order_manager.submit_order.return_value = order_result

        mock_adapter = MagicMock()
        mock_adapter.enabled = True
        mock_adapter.fetch_ticker = AsyncMock(return_value=Ticker(
            symbol="BTCUSDT",
            price=Decimal("95000.0"),
            volume_24h=Decimal("1000.0"),
            exchange="binance",
            timestamp_ms=0,
        ))
        proposal_queue._registry.get.return_value = mock_adapter

        # First reject
        client.post(
            f"/api/proposals/{sample_proposal.id}/reject",
            json={"rejected_by": "test_user"},
        )

        # Try to reject again
        response = client.post(
            f"/api/proposals/{sample_proposal.id}/reject",
            json={"rejected_by": "test_user2"},
        )
        assert response.status_code == 400

    # ===== GET /api/proposals/stats Tests =====

    @pytest.mark.asyncio
    async def test_get_proposal_stats(self, client, proposal_queue, sample_proposal, db):
        """Test GET /api/proposals/stats returns accurate counts."""
        # Create proposals in different states
        p1 = sample_proposal
        p1.id = str(uuid.uuid4())
        await proposal_queue.enqueue(p1)

        p2 = sample_proposal
        p2.id = str(uuid.uuid4())
        await proposal_queue.enqueue(p2)

        # Initial stats (all proposed)
        response = client.get("/api/proposals/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["proposed"] == 2
        assert data["approved"] == 0
        assert data["rejected"] == 0

        # Reject one
        await proposal_queue.reject(p1.id, "test_user")

        response = client.get("/api/proposals/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["proposed"] == 1
        assert data["rejected"] == 1

    # ===== GET /api/llm/history Tests =====

    @pytest.mark.asyncio
    async def test_get_llm_history_empty(self, client, db):
        """Test GET /api/llm/history returns empty list when no history."""
        response = client.get("/api/llm/history")
        assert response.status_code == 200
        data = response.json()
        assert data["history"] == []

    @pytest.mark.asyncio
    async def test_get_llm_history_with_data(self, client, db):
        """Test GET /api/llm/history returns analysis history records."""
        # Insert some history records
        await db.execute(
            """
            INSERT INTO llm_analysis_history
            (strategy_config_id, completion_status, latency_ms,
             token_count_input, token_count_output, raw_prompt, raw_response,
             parsed_proposals_count, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "success",
                1500,
                100,
                200,
                "test prompt",
                '{"proposals": []}',
                0,
                None,
            ),
        )
        await db.commit()

        response = client.get("/api/llm/history")
        assert response.status_code == 200
        data = response.json()
        assert len(data["history"]) == 1
        assert data["history"][0]["completion_status"] == "success"
        assert data["history"][0]["latency_ms"] == 1500

    @pytest.mark.asyncio
    async def test_get_llm_history_limit(self, client, db):
        """Test GET /api/llm/history respects limit parameter."""
        # Insert multiple history records
        for i in range(5):
            await db.execute(
                """
                INSERT INTO llm_analysis_history
                (strategy_config_id, completion_status, latency_ms,
                 token_count_input, token_count_output, raw_prompt, raw_response,
                 parsed_proposals_count, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    "success",
                    1000,
                    100,
                    200,
                    "test prompt",
                    '{"proposals": []}',
                    0,
                    None,
                ),
            )
        await db.commit()

        response = client.get("/api/llm/history?limit=3")
        assert response.status_code == 200
        data = response.json()
        assert len(data["history"]) == 3


class TestWebServerStrategyConfig:
    """Integration tests for strategy config REST endpoints."""

    @pytest_asyncio.fixture
    async def db(self):
        """Create in-memory SQLite database with schema."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row

        # Create exchanges table for FK constraint
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS exchanges (
                name TEXT PRIMARY KEY,
                enabled INTEGER DEFAULT 1
            )
        """)
        await conn.execute("INSERT INTO exchanges (name, enabled) VALUES ('binance', 1)")

        # Create strategy_configs table
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

        # Seed active strategy config
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
    def order_manager(self, db):
        """Create mock OrderManager."""
        manager = MagicMock(spec=OrderManager)
        manager.submit_order = AsyncMock()
        return manager

    @pytest.fixture
    def strategy_config(self):
        """Create strategy config."""
        return {
            "name": "test_strategy",
            "min_confidence_threshold": 75,
        }

    @pytest.fixture
    def exchange_registry(self):
        """Create mock exchange registry."""
        registry = MagicMock(spec=ExchangeRegistry)
        return registry

    @pytest.fixture
    def proposal_queue(self, db, order_manager, strategy_config, exchange_registry):
        """Create ProposalQueue instance."""
        queue = ProposalQueue(db, order_manager, strategy_config)
        queue.set_registry(exchange_registry)
        return queue

    @pytest.fixture
    def settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.app.host = "localhost"
        settings.app.port = 8000
        settings.app.log_level = "INFO"
        return settings

    @pytest.fixture
    def fetcher(self):
        """Create mock MarketDataFetcher."""
        fetcher = MagicMock()
        fetcher.get_all_tickers = MagicMock(return_value=[])
        fetcher.subscribe = MagicMock(return_value=asyncio.Queue())
        fetcher.unsubscribe = MagicMock()
        return fetcher

    @pytest.fixture
    def portfolio_tracker(self):
        """Create mock PortfolioTracker."""
        tracker = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exchanges = {}
        mock_snapshot.total_usdt_value = Decimal("0")
        mock_snapshot.timestamp_ms = 0
        tracker.get_snapshot = AsyncMock(return_value=mock_snapshot)
        return tracker

    @pytest_asyncio.fixture
    async def client(self, db, settings, fetcher, order_manager, portfolio_tracker, proposal_queue, exchange_registry):
        """Create test client with web server."""
        from horizon.internal.web.server import create_app

        app = create_app(
            settings=settings,
            db=db,
            registry=exchange_registry,
            fetcher=fetcher,
            order_manager=order_manager,
            portfolio_tracker=portfolio_tracker,
            proposal_queue=proposal_queue,
        )

        with TestClient(app) as client:
            yield client

    # ===== GET /api/strategy/config Tests =====

    @pytest.mark.asyncio
    async def test_get_strategy_config(self, client, db):
        """Test GET /api/strategy/config returns active config."""
        response = client.get("/api/strategy/config")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test_strategy"
        assert data["enabled"] == 1
        assert data["mode"] == "paper"
        assert data["min_confidence_threshold"] == 75
        assert data["max_risk_tier"] == "low"
        assert data["analysis_interval_hours"] == 8
        assert data["asset_whitelist"] == ["BTCUSDT", "ETHUSDT"]
        assert data["max_position_pct"] == 20.0
        assert data["max_daily_loss_pct"] == 5.0
        assert data["max_exchange_exposure_pct"] == 50.0
        assert data["cooldown_seconds"] == 300
        assert data["order_min_notional"] == 10.0
        assert data["order_max_notional"] == 10000.0
        assert data["price_drift_expiry_pct"] == 3.0
        assert data["system_prompt"] == "You are a test trading analyst."
        # Verify sensitive field is NOT exposed
        assert "autonomy_enabled" not in data

    @pytest.mark.asyncio
    async def test_get_strategy_config_not_found(self, client, db):
        """Test GET /api/strategy/config returns 404 when no active config."""
        # Delete the active config
        await db.execute("DELETE FROM strategy_configs")
        await db.commit()

        response = client.get("/api/strategy/config")
        assert response.status_code == 404

    # ===== PUT /api/strategy/config Tests =====

    @pytest.mark.asyncio
    async def test_update_strategy_config(self, client, db):
        """Test PUT /api/strategy/config updates config fields."""
        update_data = {
            "min_confidence_threshold": 80,
            "max_risk_tier": "medium",
            "analysis_interval_hours": 12,
            "asset_whitelist": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            "max_position_pct": 25.0,
            "max_daily_loss_pct": 3.0,
            "max_exchange_exposure_pct": 40.0,
            "cooldown_seconds": 600,
            "order_min_notional": 20.0,
            "order_max_notional": 5000.0,
            "price_drift_expiry_pct": 5.0,
            "system_prompt": "You are an updated trading analyst.",
        }

        response = client.put("/api/strategy/config", json=update_data)
        assert response.status_code == 200
        data = response.json()
        assert data["min_confidence_threshold"] == 80
        assert data["max_risk_tier"] == "medium"
        assert data["analysis_interval_hours"] == 12
        assert data["asset_whitelist"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        assert data["max_position_pct"] == 25.0
        assert data["max_daily_loss_pct"] == 3.0
        assert data["max_exchange_exposure_pct"] == 40.0
        assert data["cooldown_seconds"] == 600
        assert data["order_min_notional"] == 20.0
        assert data["order_max_notional"] == 5000.0
        assert data["price_drift_expiry_pct"] == 5.0
        assert data["system_prompt"] == "You are an updated trading analyst."
        # Verify sensitive field is NOT exposed
        assert "autonomy_enabled" not in data

    @pytest.mark.asyncio
    async def test_update_strategy_config_invalid_confidence(self, client, db):
        """Test PUT /api/strategy/config rejects invalid min_confidence_threshold."""
        update_data = {
            "min_confidence_threshold": 150,  # Invalid: > 100
            "max_risk_tier": "medium",
            "analysis_interval_hours": 12,
            "asset_whitelist": ["BTCUSDT"],
            "max_position_pct": 25.0,
            "max_daily_loss_pct": 3.0,
            "max_exchange_exposure_pct": 40.0,
            "cooldown_seconds": 600,
            "order_min_notional": 20.0,
            "order_max_notional": 5000.0,
            "price_drift_expiry_pct": 5.0,
            "system_prompt": "You are a test analyst.",
        }

        response = client.put("/api/strategy/config", json=update_data)
        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_update_strategy_config_invalid_risk_tier(self, client, db):
        """Test PUT /api/strategy/config rejects invalid max_risk_tier."""
        update_data = {
            "min_confidence_threshold": 80,
            "max_risk_tier": "invalid",  # Invalid: not in ['low', 'medium', 'high']
            "analysis_interval_hours": 12,
            "asset_whitelist": ["BTCUSDT"],
            "max_position_pct": 25.0,
            "max_daily_loss_pct": 3.0,
            "max_exchange_exposure_pct": 40.0,
            "cooldown_seconds": 600,
            "order_min_notional": 20.0,
            "order_max_notional": 5000.0,
            "price_drift_expiry_pct": 5.0,
            "system_prompt": "You are a test analyst.",
        }

        response = client.put("/api/strategy/config", json=update_data)
        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_update_strategy_config_invalid_interval(self, client, db):
        """Test PUT /api/strategy/config rejects analysis_interval_hours < 1."""
        update_data = {
            "min_confidence_threshold": 80,
            "max_risk_tier": "medium",
            "analysis_interval_hours": 0,  # Invalid: must be >= 1
            "asset_whitelist": ["BTCUSDT"],
            "max_position_pct": 25.0,
            "max_daily_loss_pct": 3.0,
            "max_exchange_exposure_pct": 40.0,
            "cooldown_seconds": 600,
            "order_min_notional": 20.0,
            "order_max_notional": 5000.0,
            "price_drift_expiry_pct": 5.0,
            "system_prompt": "You are a test analyst.",
        }

        response = client.put("/api/strategy/config", json=update_data)
        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_update_strategy_config_empty_system_prompt(self, client, db):
        """Test PUT /api/strategy/config rejects empty system_prompt."""
        update_data = {
            "min_confidence_threshold": 80,
            "max_risk_tier": "medium",
            "analysis_interval_hours": 12,
            "asset_whitelist": ["BTCUSDT"],
            "max_position_pct": 25.0,
            "max_daily_loss_pct": 3.0,
            "max_exchange_exposure_pct": 40.0,
            "cooldown_seconds": 600,
            "order_min_notional": 20.0,
            "order_max_notional": 5000.0,
            "price_drift_expiry_pct": 5.0,
            "system_prompt": "",  # Invalid: must be non-empty
        }

        response = client.put("/api/strategy/config", json=update_data)
        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_update_strategy_config_not_found(self, client, db):
        """Test PUT /api/strategy/config returns 404 when no active config."""
        # Delete the active config
        await db.execute("DELETE FROM strategy_configs")
        await db.commit()

        update_data = {
            "min_confidence_threshold": 80,
            "max_risk_tier": "medium",
            "analysis_interval_hours": 12,
            "asset_whitelist": ["BTCUSDT"],
            "max_position_pct": 25.0,
            "max_daily_loss_pct": 3.0,
            "max_exchange_exposure_pct": 40.0,
            "cooldown_seconds": 600,
            "order_min_notional": 20.0,
            "order_max_notional": 5000.0,
            "price_drift_expiry_pct": 5.0,
            "system_prompt": "You are a test analyst.",
        }

        response = client.put("/api/strategy/config", json=update_data)
        assert response.status_code == 404

    # ===== POST /api/strategy/mode Tests =====

    @pytest.mark.asyncio
    async def test_update_strategy_mode_to_live(self, client, db):
        """Test POST /api/strategy/mode switches mode to live."""
        response = client.post("/api/strategy/mode", json={"mode": "live"})
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "live"

        # Verify mode was persisted
        cursor = await db.execute(
            "SELECT mode FROM strategy_configs WHERE enabled = 1"
        )
        row = await cursor.fetchone()
        assert row["mode"] == "live"

    @pytest.mark.asyncio
    async def test_update_strategy_mode_to_paper(self, client, db):
        """Test POST /api/strategy/mode switches mode to paper."""
        # First set to live
        await db.execute(
            "UPDATE strategy_configs SET mode = 'live' WHERE enabled = 1"
        )
        await db.commit()

        response = client.post("/api/strategy/mode", json={"mode": "paper"})
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "paper"

    @pytest.mark.asyncio
    async def test_update_strategy_mode_invalid(self, client, db):
        """Test POST /api/strategy/mode rejects invalid mode."""
        response = client.post("/api/strategy/mode", json={"mode": "invalid"})
        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_update_strategy_mode_not_found(self, client, db):
        """Test POST /api/strategy/mode returns 404 when no active config."""
        # Delete the active config
        await db.execute("DELETE FROM strategy_configs")
        await db.commit()

        response = client.post("/api/strategy/mode", json={"mode": "live"})
        assert response.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])