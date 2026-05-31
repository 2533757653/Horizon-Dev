"""Tests for Proposal Queue State Machine."""

import asyncio
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio

from horizon.internal.exchange.registry import ExchangeRegistry
from horizon.internal.exchange.types import Ticker
from horizon.internal.ordermanager.manager import OrderManager, OrderResult
from horizon.internal.proposals.models import ProposalStatus
from horizon.internal.proposals.queue import (
    InvalidProposalStateError,
    ProposalExpiredError,
    ProposalNotFoundError,
    ProposalQueue,
)
from horizon.internal.proposals import TradeProposal


class TestProposalQueue:
    """Unit tests for ProposalQueue state machine."""

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

    # ===== Enqueue Tests =====

    @pytest.mark.asyncio
    async def test_enqueue_inserts_proposal(self, proposal_queue, sample_proposal, db):
        """Test enqueue inserts proposal into database."""
        await proposal_queue.enqueue(sample_proposal)

        cursor = await db.execute("SELECT * FROM proposals WHERE id = ?", (sample_proposal.id,))
        row = await cursor.fetchone()

        assert row is not None
        assert row["status"] == ProposalStatus.PROPOSED.value
        assert row["symbol"] == "BTCUSDT"
        assert row["volume"] == 0.1

    # ===== Approve Tests =====

    @pytest.mark.asyncio
    async def test_approve_transitions_proposal_to_executed(
        self, proposal_queue, sample_proposal, db, order_manager
    ):
        """Test approve transitions PROPOSED -> APPROVED -> EXECUTED."""
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

        approved = await proposal_queue.approve(sample_proposal.id, "test_user")

        assert approved.status == ProposalStatus.EXECUTED.value
        assert approved.approved_by == "test_user"
        assert approved.executed_order_id == "order-123"

    @pytest.mark.asyncio
    async def test_approve_on_non_proposed_raises_error(
        self, proposal_queue, sample_proposal, db, order_manager
    ):
        """Test approve on non-PROPOSED proposal raises InvalidProposalStateError."""
        await proposal_queue.enqueue(sample_proposal)

        # First approve it
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

        await proposal_queue.approve(sample_proposal.id, "test_user")

        # Try to approve again
        with pytest.raises(InvalidProposalStateError, match="Cannot approve proposal with status executed"):
            await proposal_queue.approve(sample_proposal.id, "test_user")

    @pytest.mark.asyncio
    async def test_approve_on_expired_proposal_raises_error(
        self, proposal_queue, sample_proposal, db, order_manager
    ):
        """Test approve on expired proposal raises ProposalExpiredError."""
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

        with pytest.raises(ProposalExpiredError, match="has expired"):
            await proposal_queue.approve(sample_proposal.id, "test_user")

    @pytest.mark.asyncio
    async def test_approve_not_found_raises_error(self, proposal_queue):
        """Test approve on non-existent proposal raises ProposalNotFoundError."""
        with pytest.raises(ProposalNotFoundError, match="not found"):
            await proposal_queue.approve("non-existent-id", "test_user")

    # ===== Reject Tests =====

    @pytest.mark.asyncio
    async def test_reject_transitions_proposal_to_rejected(
        self, proposal_queue, sample_proposal, db
    ):
        """Test reject transitions PROPOSED -> REJECTED."""
        await proposal_queue.enqueue(sample_proposal)

        rejected = await proposal_queue.reject(sample_proposal.id, "test_user", "Too risky")

        assert rejected.status == ProposalStatus.REJECTED.value
        assert rejected.approved_by == "test_user"

    @pytest.mark.asyncio
    async def test_reject_on_non_proposed_raises_error(
        self, proposal_queue, sample_proposal, db, order_manager
    ):
        """Test reject on non-PROPOSED proposal raises InvalidProposalStateError."""
        await proposal_queue.enqueue(sample_proposal)

        # First approve it
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

        await proposal_queue.approve(sample_proposal.id, "test_user")

        # Try to reject an already approved proposal
        with pytest.raises(InvalidProposalStateError, match="Cannot reject proposal with status executed"):
            await proposal_queue.reject(sample_proposal.id, "test_user")

    @pytest.mark.asyncio
    async def test_reject_not_found_raises_error(self, proposal_queue):
        """Test reject on non-existent proposal raises ProposalNotFoundError."""
        with pytest.raises(ProposalNotFoundError, match="not found"):
            await proposal_queue.reject("non-existent-id", "test_user")

    # ===== Expiry Tests =====

    @pytest.mark.asyncio
    async def test_check_expiry_time_expired(
        self, proposal_queue, sample_proposal, db
    ):
        """Test check_expiry identifies time-expired proposals."""
        # Set expires_at to past
        sample_proposal.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await proposal_queue.enqueue(sample_proposal)

        expired = await proposal_queue.check_expiry(
            sample_proposal.id, Decimal("95000.0")
        )

        assert expired is True

        # Verify status was updated
        cursor = await db.execute(
            "SELECT status FROM proposals WHERE id = ?", (sample_proposal.id,)
        )
        row = await cursor.fetchone()
        assert row["status"] == ProposalStatus.EXPIRED.value

    @pytest.mark.asyncio
    async def test_check_expiry_price_drift_expired(
        self, proposal_queue, sample_proposal, db
    ):
        """Test check_expiry identifies price-drift-expired proposals."""
        # proposed_price = 95000, price_drift_threshold_pct = 3.0
        # If current price moves more than 3%, proposal expires
        await proposal_queue.enqueue(sample_proposal)

        # Current price moved more than 3% (from 95000 to 98000 = 3.16%)
        expired = await proposal_queue.check_expiry(
            sample_proposal.id, Decimal("98000.0")
        )

        assert expired is True

    @pytest.mark.asyncio
    async def test_check_expiry_not_expired(
        self, proposal_queue, sample_proposal, db
    ):
        """Test check_expiry returns False for valid proposals."""
        await proposal_queue.enqueue(sample_proposal)

        # Current price is within3% drift
        expired = await proposal_queue.check_expiry(
            sample_proposal.id, Decimal("95500.0")
        )

        assert expired is False

    @pytest.mark.asyncio
    async def test_check_expiry_non_proposed_returns_false(
        self, proposal_queue, sample_proposal, db
    ):
        """Test check_expiry returns False for non-PROPOSED proposals."""
        await proposal_queue.enqueue(sample_proposal)

        # Manually update status to APPROVED
        await db.execute(
            "UPDATE proposals SET status = ? WHERE id = ?",
            (ProposalStatus.APPROVED.value, sample_proposal.id),
        )
        await db.commit()

        expired = await proposal_queue.check_expiry(
            sample_proposal.id, Decimal("98000.0")
        )

        assert expired is False

    # ===== Get Proposals Tests =====

    @pytest.mark.asyncio
    async def test_get_proposals_no_filter(self, proposal_queue, sample_proposal, db):
        """Test get_proposals returns all proposals."""
        await proposal_queue.enqueue(sample_proposal)

        proposals = await proposal_queue.get_proposals()

        assert len(proposals) == 1
        assert proposals[0].id == sample_proposal.id

    @pytest.mark.asyncio
    async def test_get_proposals_with_status_filter(
        self, proposal_queue, sample_proposal, db
    ):
        """Test get_proposals with status filter."""
        await proposal_queue.enqueue(sample_proposal)

        # Filter by PROPOSED status
        proposals = await proposal_queue.get_proposals(status=ProposalStatus.PROPOSED)

        assert len(proposals) == 1

        # Filter by different status
        proposals = await proposal_queue.get_proposals(status=ProposalStatus.APPROVED)

        assert len(proposals) == 0

    @pytest.mark.asyncio
    async def test_get_proposal(self, proposal_queue, sample_proposal, db):
        """Test get_proposal returns single proposal."""
        await proposal_queue.enqueue(sample_proposal)

        proposal = await proposal_queue.get_proposal(sample_proposal.id)

        assert proposal is not None
        assert proposal.id == sample_proposal.id

    @pytest.mark.asyncio
    async def test_get_proposal_not_found(self, proposal_queue):
        """Test get_proposal returns None for non-existent proposal."""
        proposal = await proposal_queue.get_proposal("non-existent-id")

        assert proposal is None

    # ===== Stats Tests =====

    @pytest.mark.asyncio
    async def test_get_stats(self, proposal_queue, sample_proposal, db):
        """Test get_stats returns accurate counts."""
        # Create multiple proposals in different states
        p1 = sample_proposal
        p1.id = str(uuid.uuid4())
        await proposal_queue.enqueue(p1)

        p2 = sample_proposal
        p2.id = str(uuid.uuid4())
        await proposal_queue.enqueue(p2)

        # Get initial stats (all PROPOSED)
        stats = await proposal_queue.get_stats()
        assert stats["proposed"] == 2
        assert stats["approved"] == 0

        # Reject one
        await proposal_queue.reject(p1.id, "test_user")

        stats = await proposal_queue.get_stats()
        assert stats["proposed"] == 1
        assert stats["rejected"] == 1


class TestProposalStatus:
    """Tests for ProposalStatus enum."""

    def test_proposal_status_values(self):
        """Test all status values are defined."""
        assert ProposalStatus.PROPOSED.value == "proposed"
        assert ProposalStatus.APPROVED.value == "approved"
        assert ProposalStatus.REJECTED.value == "rejected"
        assert ProposalStatus.EXPIRED.value == "expired"
        assert ProposalStatus.EXECUTED.value == "executed"
        assert ProposalStatus.AUTO_EXECUTED.value == "auto_executed"


class TestTradeProposalIsExpired:
    """Tests for TradeProposal.is_expired method."""

    @pytest.fixture
    def proposal(self):
        """Create a proposal for expiry testing."""
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

    def test_is_expired_time_not_expired(self, proposal):
        """Test is_expired returns False when time not expired."""
        # proposed_price = 95000, threshold = 3%
        # Current price 95000 (same as proposed) - should not expire
        result = proposal.is_expired(Decimal("95000.0"))
        assert result is False

    def test_is_expired_time_expired(self, proposal):
        """Test is_expired returns True when time expired."""
        proposal.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        result = proposal.is_expired(Decimal("95000.0"))
        assert result is True

    def test_is_expired_price_drift_exceeded(self, proposal):
        """Test is_expired returns True when price drift exceeded."""
        # proposed_price = 95000, threshold = 3%
        # Current price 98000 (3.16% drift) - should expire
        result = proposal.is_expired(Decimal("98000.0"))
        assert result is True

    def test_is_expired_price_drift_within_threshold(self, proposal):
        """Test is_expired returns False when price drift within threshold."""
        # proposed_price = 95000, threshold = 3%
        # Current price 96000 (1.05% drift) - should not expire
        result = proposal.is_expired(Decimal("96000.0"))
        assert result is False

    def test_is_expired_non_proposed_status(self, proposal):
        """Test is_expired returns False for non-PROPOSED status."""
        proposal.status = ProposalStatus.APPROVED.value
        result = proposal.is_expired(Decimal("98000.0"))
        assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])