"""Proposal Queue State Machine for Horizon Trading Platform.

Manages the lifecycle of trade proposals including enqueuing, approval, rejection,
and expiry scanning.
"""

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import aiosqlite

from ..exchange.registry import ExchangeRegistry
from ..ordermanager.manager import OrderManager
from .models import ProposalStatus, TradeProposal

logger = logging.getLogger(__name__)


class ProposalNotFoundError(Exception):
    """Raised when a proposal is not found."""

    pass


class InvalidProposalStateError(Exception):
    """Raised when a proposal cannot be processed in its current state."""

    pass


class ProposalExpiredError(Exception):
    """Raised when a proposal has expired."""

    pass


class ProposalQueue:
    """Manages the state machine for trade proposals."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        order_manager: OrderManager,
        strategy_config: dict,
    ):
        """Initialize the ProposalQueue.

        Args:
            db: Async SQLite database connection.
            order_manager: OrderManager instance for submitting orders.
            strategy_config: Strategy configuration dictionary.
        """
        self._db = db
        self._order_manager = order_manager
        self._strategy_config = strategy_config
        self._expiry_scanner_task: Optional[asyncio.Task] = None
        self._registry: Optional[ExchangeRegistry] = None

    def set_registry(self, registry: ExchangeRegistry) -> None:
        """Set the exchange registry for price fetching.

        Args:
            registry: ExchangeRegistry instance.
        """
        self._registry = registry

    async def enqueue(self, proposal: TradeProposal) -> None:
        """Enqueue a new proposal.

        Args:
            proposal: The proposal to enqueue.
        """
        now = datetime.now(timezone.utc)
        await self._db.execute(
            """
            INSERT INTO proposals (
                id, status, exchange, symbol, side, order_type, price, volume,
                confidence_score, risk_tier, llm_rationale, llm_raw_response,
                technical_context, market_snapshot, portfolio_snapshot,
                guardrail_result, approved_by, approved_at, executed_order_id,
                expires_at, price_drift_threshold_pct, proposed_price, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal.id,
                proposal.status,
                proposal.exchange,
                proposal.symbol,
                proposal.side,
                proposal.order_type,
                proposal.price,
                proposal.volume,
                proposal.confidence_score,
                proposal.risk_tier,
                proposal.llm_rationale,
                proposal.llm_raw_response,
                proposal.technical_context,
                proposal.market_snapshot,
                proposal.portfolio_snapshot,
                proposal.guardrail_result,
                proposal.approved_by,
                proposal.approved_at,
                proposal.executed_order_id,
                proposal.expires_at,
                proposal.price_drift_threshold_pct,
                proposal.proposed_price,
                now,
            ),
        )
        await self._db.commit()

        logger.info(
            f"Proposal {proposal.id} enqueued: {proposal.side} {proposal.volume} "
            f"{proposal.symbol} @ {proposal.exchange} "
            f"(confidence: {proposal.confidence_score}, risk: {proposal.risk_tier})"
        )

    async def approve(
        self, proposal_id: str, approved_by: str
    ) -> TradeProposal:
        """Approve a proposal and submit the corresponding order.

        Args:
            proposal_id: ID of the proposal to approve.
            approved_by: Identifier of the approver.

        Returns:
            Updated TradeProposal.

        Raises:
            ProposalNotFoundError: If proposal not found.
            InvalidProposalStateError: If proposal is not in PROPOSED state.
            ProposalExpiredError: If proposal has expired.
        """
        # Query the proposal
        cursor = await self._db.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        )
        row = await cursor.fetchone()

        if row is None:
            raise ProposalNotFoundError(f"Proposal {proposal_id} not found")

        proposal = TradeProposal.from_row(row)

        # Check state
        if proposal.status != ProposalStatus.PROPOSED.value:
            raise InvalidProposalStateError(
                f"Cannot approve proposal with status {proposal.status}"
            )

        # Check expiry - fetch current market price
        current_price = await self._get_current_price(proposal.symbol, proposal.exchange)
        if current_price is not None and proposal.is_expired(Decimal(str(current_price))):
            await self._expire(proposal_id)
            raise ProposalExpiredError(f"Proposal {proposal_id} has expired")

        # Update to APPROVED
        now = datetime.now(timezone.utc)
        await self._db.execute(
            """
            UPDATE proposals
            SET status = ?, approved_by = ?, approved_at = ?
            WHERE id = ?
            """,
            (ProposalStatus.APPROVED.value, approved_by, now, proposal_id),
        )
        await self._db.commit()

        # Submit order via order_manager
        order_request = OrderManager.OrderRequest(
            exchange=proposal.exchange,
            symbol=proposal.symbol,
            side=proposal.side,
            order_type=proposal.order_type,
            price=Decimal(str(proposal.price)) if proposal.price else None,
            volume=Decimal(str(proposal.volume)),
            source="llm_proposal",
            proposal_id=proposal_id,
        )

        order_result = await self._order_manager.submit_order(order_request)

        # Update executed_order_id
        await self._db.execute(
            """
            UPDATE proposals
            SET executed_order_id = ?, status = ?
            WHERE id = ?
            """,
            (order_result.order_id, ProposalStatus.EXECUTED.value, proposal_id),
        )
        await self._db.commit()

        # Return updated proposal
        cursor = await self._db.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        )
        row = await cursor.fetchone()
        return TradeProposal.from_row(row)

    async def reject(
        self, proposal_id: str, rejected_by: str, reason: str = ""
    ) -> TradeProposal:
        """Reject a proposal.

        Args:
            proposal_id: ID of the proposal to reject.
            rejected_by: Identifier of the rejector.
            reason: Optional rejection reason.

        Returns:
            Updated TradeProposal.

        Raises:
            ProposalNotFoundError: If proposal not found.
            InvalidProposalStateError: If proposal is not in PROPOSED state.
        """
        # Query the proposal
        cursor = await self._db.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        )
        row = await cursor.fetchone()

        if row is None:
            raise ProposalNotFoundError(f"Proposal {proposal_id} not found")

        proposal = TradeProposal.from_row(row)

        # Check state
        if proposal.status != ProposalStatus.PROPOSED.value:
            raise InvalidProposalStateError(
                f"Cannot reject proposal with status {proposal.status}"
            )

        # Update to REJECTED
        await self._db.execute(
            """
            UPDATE proposals
            SET status = ?, approved_by = ?
            WHERE id = ?
            """,
            (ProposalStatus.REJECTED.value, rejected_by, proposal_id),
        )
        await self._db.commit()

        # Return updated proposal
        cursor = await self._db.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        )
        row = await cursor.fetchone()
        return TradeProposal.from_row(row)

    async def check_expiry(
        self, proposal_id: str, current_price: Decimal
    ) -> bool:
        """Check if a proposal has expired and update its status if so.

        Args:
            proposal_id: ID of the proposal to check.
            current_price: Current market price for the symbol.

        Returns:
            True if proposal was expired, False otherwise.
        """
        cursor = await self._db.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        )
        row = await cursor.fetchone()

        if row is None:
            return False

        proposal = TradeProposal.from_row(row)

        # Only check expiry for PROPOSED proposals
        if proposal.status != ProposalStatus.PROPOSED.value:
            return False

        if proposal.is_expired(current_price):
            await self._expire(proposal_id)
            return True

        return False

    async def _expire(self, proposal_id: str) -> None:
        """Mark a proposal as expired.

        Args:
            proposal_id: ID of the proposal to expire.
        """
        await self._db.execute(
            """
            UPDATE proposals
            SET status = ?
            WHERE id = ?
            """,
            (ProposalStatus.EXPIRED.value, proposal_id),
        )
        await self._db.commit()

        logger.info(f"Proposal {proposal_id} expired")

    async def start_expiry_scanner(self) -> None:
        """Start the background expiry scanner task.

        Runs every 60 seconds and checks all PROPOSED proposals for expiry.
        """
        if self._expiry_scanner_task is not None and not self._expiry_scanner_task.done():
            return

        self._expiry_scanner_task = asyncio.create_task(self._expiry_scanner_loop())

    async def _expiry_scanner_loop(self) -> None:
        """Background loop that periodically checks proposals for expiry."""
        while True:
            try:
                await asyncio.sleep(60)
                await self._scan_proposals()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in expiry scanner: {e}")

    async def _scan_proposals(self) -> None:
        """Scan all PROPOSED proposals and expire any that have expired."""
        cursor = await self._db.execute(
            "SELECT * FROM proposals WHERE status = ?",
            (ProposalStatus.PROPOSED.value,),
        )
        rows = await cursor.fetchall()

        total = len(rows)
        expired_count = 0

        for row in rows:
            proposal = TradeProposal.from_row(row)
            current_price = await self._get_current_price(
                proposal.symbol, proposal.exchange
            )
            if current_price is not None:
                if await self.check_expiry(
                    proposal.id, Decimal(str(current_price))
                ):
                    expired_count += 1

        if total > 0:
            logger.info(f"Scanned {total} proposals, expired {expired_count}")

    async def stop_expiry_scanner(self) -> None:
        """Stop the expiry scanner task."""
        if self._expiry_scanner_task is not None:
            self._expiry_scanner_task.cancel()
            try:
                await self._expiry_scanner_task
            except asyncio.CancelledError:
                pass
            self._expiry_scanner_task = None

    async def get_proposals(
        self, status: Optional[ProposalStatus] = None, limit: int = 50
    ) -> list[TradeProposal]:
        """Get proposals with optional status filter.

        Args:
            status: Optional status filter.
            limit: Maximum number of proposals to return.

        Returns:
            List of TradeProposal objects.
        """
        if status is None:
            cursor = await self._db.execute(
                "SELECT * FROM proposals ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM proposals WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status.value, limit),
            )

        rows = await cursor.fetchall()
        return [TradeProposal.from_row(row) for row in rows]

    async def get_proposal(self, proposal_id: str) -> Optional[TradeProposal]:
        """Get a single proposal by ID.

        Args:
            proposal_id: ID of the proposal to retrieve.

        Returns:
            TradeProposal or None if not found.
        """
        cursor = await self._db.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        return TradeProposal.from_row(row)

    async def get_stats(self) -> dict:
        """Get proposal statistics by status.

        Returns:
            Dictionary with counts by status.
        """
        cursor = await self._db.execute(
            """
            SELECT status, COUNT(*) as count
            FROM proposals
            GROUP BY status
            """
        )
        rows = await cursor.fetchall()

        stats = {
            "proposed": 0,
            "approved": 0,
            "rejected": 0,
            "expired": 0,
            "executed": 0,
        }

        for row in rows:
            status = row["status"]
            if status in stats:
                stats[status] = row["count"]

        return stats

    async def _get_current_price(self, symbol: str, exchange: str) -> Optional[float]:
        """Get current market price for a symbol from a specific exchange.

        Args:
            symbol: Trading pair symbol.
            exchange: Exchange name.

        Returns:
            Current price or None if not available.
        """
        if self._registry is None:
            return None

        adapter = self._registry.get(exchange)
        if adapter is None or not adapter.enabled:
            return None

        try:
            ticker = await adapter.fetch_ticker(symbol)
            return float(ticker.price)
        except Exception as e:
            logger.warning(f"Failed to fetch price for {symbol} on {exchange}: {e}")
            return None