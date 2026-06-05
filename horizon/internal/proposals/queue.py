"""Proposal Queue State Machine for Horizon Trading Platform.

Manages the lifecycle of trade proposals including enqueuing, approval, rejection,
and expiry scanning.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import aiosqlite

from ..exchange.registry import ExchangeRegistry
from ..guardrails.evaluator import RiskGuardrailEvaluator, SystemMode
from ..guardrails.rules import OrderRequest as GuardrailOrderRequest
from ..ordermanager.manager import OrderManager
from ..paper.simulator import PaperTradingSimulator
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


class InvalidSystemModeError(Exception):
    """Raised when an operation is not allowed in the current system mode."""

    pass


class ProposalQueue:
    """Manages the state machine for trade proposals."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        order_manager: OrderManager,
        strategy_config: dict,
        guardrail_evaluator: Optional[RiskGuardrailEvaluator] = None,
        paper_simulator: Optional[PaperTradingSimulator] = None,
        mode: SystemMode = SystemMode.PAPER,
    ):
        """Initialize the ProposalQueue.

        Args:
            db: Async SQLite database connection.
            order_manager: OrderManager instance for submitting orders.
            strategy_config: Strategy configuration dictionary.
            guardrail_evaluator: RiskGuardrailEvaluator instance for auto-execution.
            paper_simulator: PaperTradingSimulator instance for paper trading.
            mode: SystemMode for auto-execution (default PAPER).
        """
        self._db = db
        self._order_manager = order_manager
        self._strategy_config = strategy_config
        self._guardrail_evaluator = guardrail_evaluator
        self._paper_simulator = paper_simulator
        self._mode = mode
        self._expiry_scanner_task: Optional[asyncio.Task] = None
        self._auto_execution_scanner_task: Optional[asyncio.Task] = None
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
                confidence_score, risk_tier, action_type, llm_rationale, llm_raw_response,
                technical_context, market_snapshot, portfolio_snapshot,
                guardrail_result, approved_by, approved_at, executed_order_id,
                expires_at, price_drift_threshold_pct, proposed_price, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                proposal.action_type,
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
            InvalidSystemModeError: If system is in COLLABORATIVE mode.
        """
        # Query the proposal
        cursor = await self._db.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        )
        row = await cursor.fetchone()

        if row is None:
            raise ProposalNotFoundError(f"Proposal {proposal_id} not found")

        proposal = TradeProposal.from_row(row)

        # Check current system mode
        if self._guardrail_evaluator is not None:
            current_mode = await self._guardrail_evaluator.get_current_mode()
            if current_mode == SystemMode.COLLABORATIVE:
                raise InvalidSystemModeError(
                    f"Cannot approve proposal in COLLABORATIVE mode - manual approval required"
                )

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
        """Scan all PROPOSED proposals and process expiry and auto-execution.

        For each PROPOSED proposal:
        - Check time expiry (now > expires_at)
        - Check price-drift expiry (abs(current-proposed)/proposed > threshold_pct/100)
        - If expired, call _expire()
        - Else if autonomy enabled AND confidence>=threshold AND risk_tier<=max:
          attempt_auto_execution()
        """
        cursor = await self._db.execute(
            "SELECT * FROM proposals WHERE status = ?",
            (ProposalStatus.PROPOSED.value,),
        )
        rows = await cursor.fetchall()

        total = len(rows)
        expired_count = 0
        auto_executed_count = 0

        # Get autonomy settings from strategy config.
        # Production passes a StrategyConfig dataclass, but older tests
        # (and earlier wiring) use a dict. Support both transparently.
        # NB: `autonomy_enabled`, `min_confidence_threshold`, `max_risk_tier`
        # are DB columns on `strategy_configs` but NOT dataclass fields, so
        # getattr returns the default. The /api/strategy/config endpoint
        # is the single source of truth for these knobs going forward.
        autonomy_enabled = self._strategy_config_value("autonomy_enabled", False)
        min_confidence = self._strategy_config_value("min_confidence_threshold", 75)
        max_risk_tier = self._strategy_config_value("max_risk_tier", "low")
        risk_tier_order = {"low": 1, "medium": 2, "high": 3}
        max_tier_value = risk_tier_order.get(max_risk_tier, 1)

        for row in rows:
            proposal = TradeProposal.from_row(row)

            # Check time expiry
            now = datetime.now(timezone.utc)
            time_expired = now > proposal.expires_at

            # Check price-drift expiry
            current_price = await self._get_current_price(
                proposal.symbol, proposal.exchange
            )
            price_expired = False
            if current_price is not None:
                price_expired = proposal.is_expired(Decimal(str(current_price)))

            # Determine if proposal is expired (time or price drift)
            if time_expired or price_expired:
                await self._expire(proposal.id)
                expired_count += 1
                continue

            # Attempt auto-execution for non-expired proposals
            if autonomy_enabled and self._guardrail_evaluator is not None:
                # Pre-filter by confidence and risk tier
                request_tier_value = risk_tier_order.get(proposal.risk_tier, 3)
                if proposal.confidence_score >= min_confidence and request_tier_value <= max_tier_value:
                    if await self.attempt_auto_execution(proposal):
                        auto_executed_count += 1

        if total > 0:
            logger.info(f"Expiry scanner: {total} proposed, {expired_count} expired, {auto_executed_count} auto-executed")

    async def stop_expiry_scanner(self) -> None:
        """Stop the expiry scanner task."""
        if self._expiry_scanner_task is not None:
            self._expiry_scanner_task.cancel()
            try:
                await self._expiry_scanner_task
            except asyncio.CancelledError:
                pass
            self._expiry_scanner_task = None

    async def attempt_auto_execution(self, proposal: TradeProposal) -> bool:
        """Attempt to automatically execute a proposal.

        Args:
            proposal: The proposal to attempt auto-execution for.

        Returns:
            True if auto-execution succeeded, False otherwise.
        """
        # 1. If status != PROPOSED: return False
        if proposal.status != ProposalStatus.PROPOSED.value:
            return False

        # 2. can_autonomously_execute check
        if self._guardrail_evaluator is None:
            logger.warning(f"No guardrail evaluator configured for auto-execution")
            return False

        request = GuardrailOrderRequest(
            symbol=proposal.symbol,
            side=proposal.side,
            order_type=proposal.order_type,
            price=Decimal(str(proposal.price)) if proposal.price else None,
            volume=Decimal(str(proposal.volume)),
            exchange=proposal.exchange,
            source="autonomous",
            notional=Decimal(str(proposal.price or 0)) * Decimal(str(proposal.volume)),
        )

        can_execute, reason = await self._guardrail_evaluator.can_autonomously_execute(
            proposal.confidence_score, proposal.risk_tier, request
        )
        if not can_execute:
            logger.info(f"Proposal {proposal.id} cannot auto-execute: {reason}")
            return False

        # 3. evaluate_proposal check
        guardrail_result = await self._guardrail_evaluator.evaluate_proposal(proposal)
        if not guardrail_result.passed:
            logger.info(
                f"Proposal {proposal.id} failed guardrail evaluation: "
                f"{guardrail_result.reason}"
            )
            # Record breach in guardrail_events (already done by evaluate_proposal)
            return False

        # 4-5. Execute based on mode
        current_mode = await self._guardrail_evaluator.get_current_mode()
        order_request = OrderManager.OrderRequest(
            exchange=proposal.exchange,
            symbol=proposal.symbol,
            side=proposal.side,
            order_type=proposal.order_type,
            price=Decimal(str(proposal.price)) if proposal.price else None,
            volume=Decimal(str(proposal.volume)),
            source="autonomous",
            proposal_id=proposal.id,
        )

        executed_order_id = None

        if current_mode == SystemMode.PAPER:
            # 4. PAPER mode: use paper simulator
            if self._paper_simulator is None:
                logger.warning(f"No paper simulator configured for auto-execution")
                return False

            if proposal.order_type == "market":
                paper_result = await self._paper_simulator.simulate_market_order(
                    proposal_id=proposal.id,
                    exchange=proposal.exchange,
                    symbol=proposal.symbol,
                    side=proposal.side,
                    volume=Decimal(str(proposal.volume)),
                )
            else:
                paper_result = await self._paper_simulator.simulate_limit_order(
                    proposal_id=proposal.id,
                    exchange=proposal.exchange,
                    symbol=proposal.symbol,
                    side=proposal.side,
                    price=Decimal(str(proposal.price)) if proposal.price else Decimal("0"),
                    volume=Decimal(str(proposal.volume)),
                )
            executed_order_id = paper_result.order_id

        elif current_mode == SystemMode.LIVE:
            # 5. LIVE mode: submit via order manager
            order_result = await self._order_manager.submit_order(order_request)
            executed_order_id = order_result.order_id

        else:
            logger.warning(f"Auto-execution not supported in {current_mode.value} mode")
            return False

        # Update proposal status to AUTO_EXECUTED
        now = datetime.now(timezone.utc)
        await self._db.execute(
            """
            UPDATE proposals
            SET status = ?, executed_order_id = ?, approved_at = ?
            WHERE id = ?
            """,
            (ProposalStatus.AUTO_EXECUTED.value, executed_order_id, now, proposal.id),
        )
        await self._db.commit()

        logger.info(
            f"Proposal {proposal.id} auto-executed in {current_mode.value} mode, "
            f"order_id: {executed_order_id}"
        )
        return True

    async def start_auto_execution_scanner(self) -> None:
        """Start the background auto-execution scanner task.

        Runs every 30 seconds and auto-executes qualifying PROPOSED proposals.
        """
        if self._auto_execution_scanner_task is not None and not self._auto_execution_scanner_task.done():
            return

        self._auto_execution_scanner_task = asyncio.create_task(self._auto_execution_scanner_loop())

    async def _auto_execution_scanner_loop(self) -> None:
        """Background loop that periodically attempts auto-execution for proposals."""
        while True:
            try:
                await asyncio.sleep(30)
                await self._scan_and_auto_execute()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in auto-execution scanner: {e}")

    async def _scan_and_auto_execute(self) -> None:
        """Scan PROPOSED proposals and attempt auto-execution for qualifying ones."""
        if self._guardrail_evaluator is None:
            return

        # Get autonomy thresholds from strategy config (dict- and dataclass-safe)
        min_confidence = self._strategy_config_value("min_confidence_threshold", 75)
        max_risk_tier = self._strategy_config_value("max_risk_tier", "low")
        risk_tier_order = {"low": 1, "medium": 2, "high": 3}
        max_tier_value = risk_tier_order.get(max_risk_tier, 1)

        cursor = await self._db.execute(
            "SELECT * FROM proposals WHERE status = ?",
            (ProposalStatus.PROPOSED.value,),
        )
        rows = await cursor.fetchall()

        total = len(rows)
        executed_count = 0

        for row in rows:
            proposal = TradeProposal.from_row(row)

            # Pre-filter by confidence and risk tier
            request_tier_value = risk_tier_order.get(proposal.risk_tier, 3)
            if proposal.confidence_score < min_confidence:
                continue
            if request_tier_value > max_tier_value:
                continue

            # Attempt auto-execution
            if await self.attempt_auto_execution(proposal):
                executed_count += 1

        if total > 0:
            logger.info(f"Auto-exec scanner: checked {total}, executed {executed_count}")

    async def stop_auto_execution_scanner(self) -> None:
        """Stop the auto-execution scanner task."""
        if self._auto_execution_scanner_task is not None:
            self._auto_execution_scanner_task.cancel()
            try:
                await self._auto_execution_scanner_task
            except asyncio.CancelledError:
                pass
            self._auto_execution_scanner_task = None

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

    def _strategy_config_value(self, name: str, default):
        """Read a value from the strategy config, transparently handling
        both dict-style (legacy test fixtures) and dataclass-style
        (production) configs.

        Production main.py passes a `StrategyConfig` dataclass that does
        NOT carry autonomy / confidence fields — those live in the
        `strategy_configs` table and should be edited via
        PUT /api/strategy/config. The dataclass fields that exist are
        `mode`, `asset_whitelist`, and the notional/pct guardrail knobs.
        """
        cfg = self._strategy_config
        if isinstance(cfg, dict):
            return cfg.get(name, default)
        return getattr(cfg, name, default)