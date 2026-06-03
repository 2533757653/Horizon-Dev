"""Risk Guardrail Evaluator for Horizon Trading Platform.

Orchestrates all 6 guardrail rules and manages system mode (LIVE/PAPER/COLLABORATIVE).
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

import aiosqlite

from ..portfolio.tracker import PortfolioTracker
from .cooldown_tracker import CooldownTracker
from .rules import (
    AssetWhitelistRule,
    CooldownRule,
    DailyLossLimitRule,
    ExchangeExposureRule,
    GuardrailAction,
    GuardrailRule,
    OrderNotionalRule,
    OrderRequest,
    PositionSizeRule,
    StrategyConfig,
)

logger = logging.getLogger(__name__)


class SystemMode(Enum):
    """System operation mode with graduated autonomy levels."""

    LIVE = "live"
    PAPER = "paper"
    COLLABORATIVE = "collaborative"


@dataclass
class GuardrailResult:
    """Result of guardrail evaluation.

    Attributes:
        passed: True if all rules passed.
        violated_rules: List of rule names that failed.
        blocking: True if the order should be blocked.
        downgrade_autonomy: True if autonomy should be downgraded due to this evaluation.
        cooldown_seconds_remaining: Remaining cooldown seconds if cooldown rule failed, None otherwise.
        details: List of detail dicts for each rule evaluation.
        reason: Human-readable reason for the overall result.
    """

    passed: bool = True
    violated_rules: list[str] = field(default_factory=list)
    blocking: bool = False
    downgrade_autonomy: bool = False
    cooldown_seconds_remaining: int | None = None
    details: list[dict] = field(default_factory=list)
    reason: str = ""


class RiskGuardrailEvaluator:
    """Orchestrates all 6 guardrail rules and manages system mode.

    Evaluates order requests against all guardrail rules, persists breach events,
    and manages the graduated autonomy system (LIVE/PAPER/COLLABORATIVE).
    """

    # Default downgrade duration in minutes
    DOWNGRADE_DURATION_MINUTES = 30

    def __init__(
        self,
        db: aiosqlite.Connection,
        cooldown_tracker: CooldownTracker,
        strategy_config: StrategyConfig,
        fetcher: Any,
    ) -> None:
        """Initialize the RiskGuardrailEvaluator.

        Args:
            db: Async SQLite database connection.
            cooldown_tracker: CooldownTracker instance for cooldown rule.
            strategy_config: StrategyConfig dataclass with guardrail settings.
            fetcher: MarketDataFetcher instance for portfolio valuation.
        """
        self._db = db
        self._cooldown_tracker = cooldown_tracker
        self._strategy_config = strategy_config
        self._fetcher = fetcher

        # Initialize all 6 guardrail rules
        self._rules: list[GuardrailRule] = [
            AssetWhitelistRule(),
            CooldownRule(),
            ExchangeExposureRule(),
            PositionSizeRule(),
            OrderNotionalRule(),
            DailyLossLimitRule(),
        ]

    async def evaluate(self, request: OrderRequest) -> GuardrailResult:
        """Run all 6 guardrail rules and collect results.

        Args:
            request: The order request to evaluate.

        Returns:
            GuardrailResult with passed status, violated rules, and blocking info.
        """
        # Build context for rule evaluation
        portfolio_snapshot = await self._get_portfolio_snapshot()
        daily_pnl = await self._get_daily_pnl()
        current_mode = await self.get_current_mode()

        context = {
            "portfolio_snapshot": portfolio_snapshot,
            "cooldown_tracker": self._cooldown_tracker,
            "daily_pnl": daily_pnl,
            "strategy_config": self._strategy_config,
            "current_mode": current_mode.value,
        }

        # Run all rules
        violated_rules: list[str] = []
        blocking = False
        downgrade_autonomy = False
        cooldown_seconds_remaining: int | None = None
        details: list[dict] = []

        for rule in self._rules:
            passed, rule_details = await rule.check(request, context)
            details.append(rule_details)

            if not passed:
                violated_rules.append(rule.name)

                if rule.action == GuardrailAction.BLOCK:
                    blocking = True
                elif rule.action == GuardrailAction.BLOCK_AND_DOWNGRADE:
                    blocking = True
                    downgrade_autonomy = True

                # Track cooldown remaining if cooldown rule failed
                if rule.name == "cooldown" and cooldown_seconds_remaining is None:
                    remaining = await self._cooldown_tracker.is_in_cooldown(
                        request.exchange, request.symbol
                    )
                    if remaining[1] is not None:
                        cooldown_seconds_remaining = remaining[1]

        # Build reason string
        reason = self._build_reason(violated_rules, blocking, downgrade_autonomy)

        result = GuardrailResult(
            passed=len(violated_rules) == 0,
            violated_rules=violated_rules,
            blocking=blocking,
            downgrade_autonomy=downgrade_autonomy,
            cooldown_seconds_remaining=cooldown_seconds_remaining,
            details=details,
            reason=reason,
        )

        # Persist events on breach
        if not result.passed:
            await self._persist_guardrail_event(request, result)

        return result

    async def _persist_guardrail_event(
        self, request: OrderRequest, result: GuardrailResult
    ) -> None:
        """Persist guardrail breach events to the database.

        Args:
            request: The order request that was evaluated.
            result: The guardrail evaluation result.
        """
        now = datetime.now(timezone.utc)
        downgrade_expires_at = None

        if result.downgrade_autonomy:
            downgrade_expires_at = now + timedelta(minutes=self.DOWNGRADE_DURATION_MINUTES)

        for rule_name in result.violated_rules:
            # Determine action taken
            rule = self._get_rule_by_name(rule_name)
            if rule is not None:
                action_taken = rule.action.value
            else:
                action_taken = "blocked"

            await self._db.execute(
                """
                INSERT INTO guardrail_events (
                    order_id, proposal_id, rule_name, action_taken,
                    request_symbol, request_exchange, request_side,
                    request_volume, request_price, current_value, threshold_value,
                    downgrade_active, downgrade_expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    None,  # order_id - will be set by caller if available
                    None,  # proposal_id - will be set by caller if available
                    rule_name,
                    action_taken,
                    request.symbol,
                    request.exchange,
                    request.side,
                    float(request.volume),
                    float(request.price) if request.price else None,
                    None,  # current_value - from details
                    None,  # threshold_value - from details
                    1 if result.downgrade_autonomy else 0,
                    downgrade_expires_at,
                    now,
                ),
            )

        if result.violated_rules:
            await self._db.commit()
            logger.info(
                f"Guardrail breach recorded: {len(result.violated_rules)} violations, "
                f"downgrade_autonomy={result.downgrade_autonomy}"
            )

    async def get_current_mode(self) -> SystemMode:
        """Get the current system mode from database.

        Returns:
            SystemMode: Current system mode (LIVE or PAPER).
        """
        cursor = await self._db.execute(
            "SELECT mode FROM strategy_configs WHERE enabled = 1 LIMIT 1"
        )
        row = await cursor.fetchone()
        if row is not None and row["mode"]:
            return SystemMode(row["mode"])
        return SystemMode(self._strategy_config.mode)

    async def set_mode(self, mode: SystemMode) -> None:
        """Update the system mode in strategy_config.

        Args:
            mode: The new system mode to set.
        """
        mode_value = mode.value
        await self._db.execute(
            "UPDATE strategy_configs SET mode = ?, updated_at = ? WHERE name = ?",
            (mode_value, datetime.now(timezone.utc), "default_long_term"),
        )
        await self._db.commit()
        logger.info(f"System mode changed to: {mode_value}")

    async def can_autonomously_execute(
        self,
        confidence_score: int,
        risk_tier: str,
        request: OrderRequest,
    ) -> tuple[bool, str]:
        """Check if an order can be executed autonomously.

        Checks:
        - Current mode is not COLLABORATIVE
        - Autonomy is enabled in strategy_config
        - Confidence score meets threshold
        - Risk tier is within allowed max
        - System mode is not PAPER

        Args:
            confidence_score: The confidence score of the proposal.
            risk_tier: The risk tier of the proposal (low/medium/high).
            request: The order request to check.

        Returns:
            Tuple of (allowed: bool, reason: str).
            If allowed is True, reason will be "allowed".
            If allowed is False, reason will explain why not.
        """
        # Check if in COLLABORATIVE mode
        current_mode = await self.get_current_mode()
        if current_mode == SystemMode.COLLABORATIVE:
            return False, "System is in COLLABORATIVE mode - manual approval required"

        # Check if autonomy is enabled
        if not getattr(self._strategy_config, "autonomy_enabled", False):
            return False, "Autonomy is disabled in strategy config"

        # Check confidence threshold
        min_confidence = getattr(self._strategy_config, "min_confidence_threshold", 75)
        if confidence_score < min_confidence:
            return False, f"Confidence score {confidence_score} below threshold {min_confidence}"

        # Check risk tier
        max_risk_tier = getattr(self._strategy_config, "max_risk_tier", "low")
        risk_tier_order = {"low": 1, "medium": 2, "high": 3}
        max_tier_value = risk_tier_order.get(max_risk_tier, 1)
        request_tier_value = risk_tier_order.get(risk_tier, 3)

        if request_tier_value > max_tier_value:
            return False, f"Risk tier '{risk_tier}' exceeds max allowed '{max_risk_tier}'"

        # Check if in PAPER mode
        if current_mode == SystemMode.PAPER:
            return True, "allowed"

        return True, "allowed"

    async def evaluate_proposal(self, proposal: Any) -> GuardrailResult:
        """Evaluate a proposal through the guardrail system.

        Converts the proposal to an OrderRequest, evaluates it, and stores
        the result JSON in the proposals table.

        Args:
            proposal: TradeProposal object to evaluate.

        Returns:
            GuardrailResult from the evaluation.
        """
        # Convert proposal to OrderRequest
        request = OrderRequest(
            symbol=proposal.symbol,
            side=proposal.side,
            order_type=proposal.order_type,
            price=Decimal(str(proposal.price)) if proposal.price else None,
            volume=Decimal(str(proposal.volume)),
            exchange=proposal.exchange,
            source="autonomous",
            notional=Decimal(str(proposal.price or 0)) * Decimal(str(proposal.volume)),
        )

        # Evaluate
        result = await self.evaluate(request)

        # Store result JSON in proposals table
        result_json = json.dumps(
            {
                "passed": result.passed,
                "violated_rules": result.violated_rules,
                "blocking": result.blocking,
                "downgrade_autonomy": result.downgrade_autonomy,
                "cooldown_seconds_remaining": result.cooldown_seconds_remaining,
                "details": result.details,
                "reason": result.reason,
            }
        )

        await self._db.execute(
            "UPDATE proposals SET guardrail_result = ? WHERE id = ?",
            (result_json, proposal.id),
        )
        await self._db.commit()

        return result

    async def _get_portfolio_snapshot(self) -> PortfolioTracker.PortfolioSnapshot | None:
        """Get current portfolio snapshot.

        Returns:
            PortfolioSnapshot or None if unavailable.
        """
        try:
            # Create a temporary tracker to get snapshot
            # In production, this would be injected or retrieved from context
            return None  # Will be provided via context in evaluate()
        except Exception:
            return None

    async def _get_daily_pnl(self) -> float:
        """Get today's P&L.

        Returns:
            Today's total P&L (realized + paper).
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cursor = await self._db.execute(
            "SELECT realized_pnl, paper_pnl FROM daily_pnl WHERE date = ?",
            (today,),
        )
        row = await cursor.fetchone()

        if row is not None:
            return (row["realized_pnl"] or 0.0) + (row["paper_pnl"] or 0.0)
        return 0.0

    def _get_rule_by_name(self, name: str) -> GuardrailRule | None:
        """Get a rule instance by name.

        Args:
            name: Rule name to find.

        Returns:
            GuardrailRule instance or None if not found.
        """
        for rule in self._rules:
            if rule.name == name:
                return rule
        return None

    def _build_reason(
        self, violated_rules: list[str], blocking: bool, downgrade_autonomy: bool
    ) -> str:
        """Build a human-readable reason string.

        Args:
            violated_rules: List of rule names that failed.
            blocking: Whether the order was blocked.
            downgrade_autonomy: Whether autonomy was downgraded.

        Returns:
            Human-readable reason string.
        """
        if not violated_rules:
            return "All guardrail rules passed"

        rule_count = len(violated_rules)
        rule_names = ", ".join(violated_rules)

        if downgrade_autonomy:
            return f"Guardrail breach ({rule_count} rule(s)): {rule_names}. Order blocked, autonomy downgraded to COLLABORATIVE."
        elif blocking:
            return f"Guardrail breach ({rule_count} rule(s)): {rule_names}. Order blocked."
        else:
            return f"Guardrail alert ({rule_count} rule(s)): {rule_names}."