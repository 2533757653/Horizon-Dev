"""Guardrail rules for order evaluation.

Six concrete rules evaluate every order before exchange submission:
1. AssetWhitelistRule: symbol in strategy_config.asset_whitelist -> BLOCK_AND_DOWNGRADE
2. CooldownRule: not in cooldown -> BLOCK_AND_DOWNGRADE
3. ExchangeExposureRule: (exchange_usdt + order_notional)/total_portfolio_usdt <= max_exchange_exposure_pct -> BLOCK_AND_DOWNGRADE
4. PositionSizeRule: (position_usdt + order_notional)/total_portfolio_usdt <= max_position_pct -> BLOCK_AND_DOWNGRADE
5. OrderNotionalRule: order_min_notional <= notional <= order_max_notional -> BLOCK
6. DailyLossLimitRule: daily_pnl >= -max_daily_loss_pct * total_portfolio_usdt, only for autonomous -> BLOCK_AND_DOWNGRADE
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Optional


class GuardrailAction(Enum):
    """Action to take when a guardrail rule fails."""

    BLOCK = "block"
    BLOCK_AND_DOWNGRADE = "block_and_downgrade"
    ALERT = "alert"


@dataclass
class GuardrailRule:
    """Base class for guardrail rules.

    Attributes:
        name: Unique identifier for the rule.
        action: The GuardrailAction to take on failure.
        description: Human-readable description of the rule.
    """

    name: str
    action: GuardrailAction
    description: str

    async def check(
        self, request: "OrderRequest", context: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """Evaluate the rule against an order request.

        Args:
            request: The order request to evaluate.
            context: Context dict containing:
                - portfolio_snapshot: PortfolioSnapshot with exchanges and total_usdt_value
                - cooldown_tracker: CooldownTracker instance
                - daily_pnl: float, today's realized + paper PnL in USDT
                - strategy_config: StrategyConfig dataclass with guardrail settings
                - current_mode: str, current autonomy mode (AUTONOMOUS, COLLABORATIVE, MANUAL)

        Returns:
            Tuple of (passed: bool, details: dict).
            details contains current_value, threshold_value, and optional rule_name.
        """
        raise NotImplementedError


@dataclass
class OrderRequest:
    """Order request for guardrail evaluation.

    Attributes:
        symbol: Trading pair symbol (e.g. "BTC/USDT").
        side: Order side ("buy" or "sell").
        order_type: Order type ("market" or "limit").
        price: Order price (None for market orders).
        volume: Order volume.
        exchange: Exchange name.
        source: Order source ("manual", "llm_proposal", "autonomous").
        notional: Order notional value in USDT (price * volume).
    """

    symbol: str
    side: str
    order_type: str
    price: Optional[Decimal]
    volume: Decimal
    exchange: str
    source: str
    notional: Decimal


@dataclass
class StrategyConfig:
    """Strategy configuration for guardrail settings.

    Attributes:
        asset_whitelist: List of allowed trading symbols.
        order_min_notional: Minimum order notional in USDT.
        order_max_notional: Maximum order notional in USDT.
        max_exchange_exposure_pct: Max exposure per exchange as fraction (e.g. 0.5 for 50%).
        max_position_pct: Max position size as fraction (e.g. 0.3 for 30%).
        cooldown_seconds: Cooldown period in seconds per exchange/symbol.
        max_daily_loss_pct: Max daily loss as fraction (e.g. 0.05 for 5%).
    """

    asset_whitelist: list[str]
    order_min_notional: Decimal = Decimal("10")
    order_max_notional: Decimal = Decimal("1000000")
    max_exchange_exposure_pct: float = 0.5
    max_position_pct: float = 0.3
    cooldown_seconds: int = 300
    max_daily_loss_pct: float = 0.05


class AssetWhitelistRule(GuardrailRule):
    """Rule that blocks orders for symbols not in the asset whitelist.

    This rule uses BLOCK_AND_DOWNGRADE because trading unauthorized assets
    is a serious policy violation.
    """

    def __init__(self) -> None:
        super().__init__(
            name="asset_whitelist",
            action=GuardrailAction.BLOCK_AND_DOWNGRADE,
            description="Symbol must be in strategy asset whitelist",
        )

    async def check(
        self, request: OrderRequest, context: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        strategy_config: StrategyConfig = context.get("strategy_config")
        if strategy_config is None:
            # If no strategy config, allow by default (fail open)
            return True, {"current_value": None, "threshold_value": None}

        whitelist = strategy_config.asset_whitelist
        passed = request.symbol in whitelist

        return passed, {
            "current_value": request.symbol,
            "threshold_value": whitelist,
            "rule_name": self.name,
        }


class CooldownRule(GuardrailRule):
    """Rule that blocks orders during cooldown period after trading.

    This rule uses BLOCK_AND_DOWNGRADE because repeatedly trading the same
    pair despite cooldown indicates a system issue or policy violation.
    """

    def __init__(self) -> None:
        super().__init__(
            name="cooldown",
            action=GuardrailAction.BLOCK_AND_DOWNGRADE,
            description="Symbol must not be in cooldown for this exchange",
        )

    async def check(
        self, request: OrderRequest, context: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        cooldown_tracker = context.get("cooldown_tracker")
        if cooldown_tracker is None:
            # If no cooldown tracker, allow by default (fail open)
            return True, {"current_value": None, "threshold_value": None}

        passed = not await cooldown_tracker.is_in_cooldown(request.exchange, request.symbol)
        strategy_config = context.get("strategy_config")
        threshold_seconds = strategy_config.cooldown_seconds if strategy_config else 300

        return passed, {
            "current_value": "in_cooldown" if not passed else "ok",
            "threshold_value": threshold_seconds,
            "rule_name": self.name,
        }


class ExchangeExposureRule(GuardrailRule):
    """Rule that enforces maximum exposure per exchange.

    Ensures (exchange_usdt + order_notional) / total_portfolio_usdt <= max_exchange_exposure_pct.
    This rule uses BLOCK_AND_DOWNGRADE because exceeding exchange exposure limits
    could trigger account restrictions or liquidity issues.
    """

    def __init__(self) -> None:
        super().__init__(
            name="exchange_exposure",
            action=GuardrailAction.BLOCK_AND_DOWNGRADE,
            description="Exchange exposure must not exceed max_exchange_exposure_pct",
        )

    async def check(
        self, request: OrderRequest, context: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        portfolio_snapshot = context.get("portfolio_snapshot")
        strategy_config: StrategyConfig = context.get("strategy_config")

        if portfolio_snapshot is None or strategy_config is None:
            return True, {"current_value": None, "threshold_value": None}

        total_usdt = portfolio_snapshot.total_usdt_value
        if total_usdt <= 0:
            # No portfolio value, fail open
            return True, {"current_value": None, "threshold_value": None}

        # Calculate exchange USDT value
        exchange_balances = portfolio_snapshot.exchanges.get(request.exchange, [])
        exchange_usdt = Decimal("0")
        for balance in exchange_balances:
            if balance.asset == "USDT":
                exchange_usdt += balance.free + balance.locked

        # Calculate current exposure ratio
        order_notional = request.notional
        current_exposure = (exchange_usdt + order_notional) / total_usdt
        threshold = strategy_config.max_exchange_exposure_pct

        passed = float(current_exposure) <= threshold

        return passed, {
            "current_value": float(current_exposure),
            "threshold_value": threshold,
            "rule_name": self.name,
        }


class PositionSizeRule(GuardrailRule):
    """Rule that enforces maximum position size per symbol.

    Ensures (position_usdt + order_notional) / total_portfolio_usdt <= max_position_pct.
    This rule uses BLOCK_AND_DOWNGRADE because oversized positions increase
    liquidation risk and portfolio volatility.
    """

    def __init__(self) -> None:
        super().__init__(
            name="position_size",
            action=GuardrailAction.BLOCK_AND_DOWNGRADE,
            description="Position size must not exceed max_position_pct of portfolio",
        )

    async def check(
        self, request: OrderRequest, context: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        portfolio_snapshot = context.get("portfolio_snapshot")
        strategy_config: StrategyConfig = context.get("strategy_config")

        if portfolio_snapshot is None or strategy_config is None:
            return True, {"current_value": None, "threshold_value": None}

        total_usdt = portfolio_snapshot.total_usdt_value
        if total_usdt <= 0:
            return True, {"current_value": None, "threshold_value": None}

        # Calculate position USDT for this symbol on this exchange
        # NOTE: position_usdt assumes balances are already normalized to USDT terms
        # (i.e., this rule does not convert non-USDT assets to USDT values).
        # This is an existing limitation - see code quality review.
        exchange_balances = portfolio_snapshot.exchanges.get(request.exchange, [])
        position_usdt = Decimal("0")
        for balance in exchange_balances:
            if balance.asset == request.symbol.split("/")[0]:
                # Assume base asset value
                position_usdt += balance.free + balance.locked

        # Calculate current position ratio
        order_notional = request.notional
        current_position_ratio = (position_usdt + order_notional) / total_usdt
        threshold = strategy_config.max_position_pct

        passed = float(current_position_ratio) <= threshold

        return passed, {
            "current_value": float(current_position_ratio),
            "threshold_value": threshold,
            "rule_name": self.name,
        }


class OrderNotionalRule(GuardrailRule):
    """Rule that enforces minimum and maximum order notional.

    Ensures order_min_notional <= notional <= order_max_notional.
    This rule uses BLOCK (no downgrade) because small orders may simply
    be inefficient, while large orders can be manually reviewed.
    """

    def __init__(self) -> None:
        super().__init__(
            name="order_notional",
            action=GuardrailAction.BLOCK,
            description="Order notional must be within configured min/max bounds",
        )

    async def check(
        self, request: OrderRequest, context: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        strategy_config: StrategyConfig = context.get("strategy_config")

        if strategy_config is None:
            return True, {"current_value": None, "threshold_value": None}

        notional = request.notional
        min_notional = strategy_config.order_min_notional
        max_notional = strategy_config.order_max_notional

        passed = min_notional <= notional <= max_notional

        return passed, {
            "current_value": float(notional),
            "threshold_value": {"min": float(min_notional), "max": float(max_notional)},
            "rule_name": self.name,
        }


class DailyLossLimitRule(GuardrailRule):
    """Rule that enforces maximum daily loss limit.

    Ensures daily_pnl >= -max_daily_loss_pct * total_portfolio_usdt.
    This rule ONLY applies to autonomous orders.
    This rule uses BLOCK_AND_DOWNGRADE because continued losses in autonomous
    mode indicate market conditions or system issues requiring human review.
    """

    def __init__(self) -> None:
        super().__init__(
            name="daily_loss_limit",
            action=GuardrailAction.BLOCK_AND_DOWNGRADE,
            description="Daily PnL must not exceed max_daily_loss_pct loss (autonomous only)",
        )

    async def check(
        self, request: OrderRequest, context: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        # Only apply to autonomous orders
        if request.source != "autonomous":
            return True, {"current_value": None, "threshold_value": None}

        portfolio_snapshot = context.get("portfolio_snapshot")
        strategy_config: StrategyConfig = context.get("strategy_config")
        daily_pnl: float = context.get("daily_pnl", 0.0)

        if portfolio_snapshot is None or strategy_config is None:
            return True, {"current_value": None, "threshold_value": None}

        total_usdt = portfolio_snapshot.total_usdt_value
        if total_usdt <= 0:
            return True, {"current_value": None, "threshold_value": None}

        threshold_loss = -strategy_config.max_daily_loss_pct * float(total_usdt)
        passed = daily_pnl >= threshold_loss

        return passed, {
            "current_value": daily_pnl,
            "threshold_value": threshold_loss,
            "rule_name": self.name,
        }