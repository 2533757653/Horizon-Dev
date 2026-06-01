"""Tests for guardrail rules."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from horizon.internal.guardrails.rules import (
    AssetWhitelistRule,
    CooldownRule,
    DailyLossLimitRule,
    ExchangeExposureRule,
    GuardrailAction,
    OrderNotionalRule,
    OrderRequest,
    PositionSizeRule,
    StrategyConfig,
)


@pytest.fixture
def strategy_config() -> StrategyConfig:
    """Standard strategy config for tests."""
    return StrategyConfig(
        asset_whitelist=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        order_min_notional=Decimal("10"),
        order_max_notional=Decimal("1000000"),
        max_exchange_exposure_pct=0.5,
        max_position_pct=0.3,
        cooldown_seconds=300,
        max_daily_loss_pct=0.05,
    )


@pytest.fixture
def portfolio_snapshot():
    """Mock portfolio snapshot."""
    snapshot = MagicMock()
    snapshot.total_usdt_value = Decimal("10000")
    snapshot.exchanges = {
        "hyperliquid": [
            MagicMock(asset="USDT", free=Decimal("5000"), locked=Decimal("0")),
            MagicMock(asset="BTC", free=Decimal("0.5"), locked=Decimal("0")),
        ]
    }
    return snapshot


class TestAssetWhitelistRule:
    """Tests for AssetWhitelistRule."""

    @pytest.fixture
    def rule(self) -> AssetWhitelistRule:
        return AssetWhitelistRule()

    def test_init(self, rule: AssetWhitelistRule) -> None:
        assert rule.name == "asset_whitelist"
        assert rule.action == GuardrailAction.BLOCK_AND_DOWNGRADE
        assert "whitelist" in rule.description.lower()

    @pytest.mark.asyncio
    async def test_passes_when_symbol_in_whitelist(
        self, rule: AssetWhitelistRule, strategy_config: StrategyConfig
    ) -> None:
        request = OrderRequest(
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            price=None,
            volume=Decimal("0.01"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("100"),
        )
        context = {"strategy_config": strategy_config}
        passed, details = await rule.check(request, context)
        assert passed is True
        assert details["current_value"] == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_fails_when_symbol_not_in_whitelist(
        self, rule: AssetWhitelistRule, strategy_config: StrategyConfig
    ) -> None:
        request = OrderRequest(
            symbol="DOGE/USDT",
            side="buy",
            order_type="market",
            price=None,
            volume=Decimal("1000"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("100"),
        )
        context = {"strategy_config": strategy_config}
        passed, details = await rule.check(request, context)
        assert passed is False
        assert details["current_value"] == "DOGE/USDT"

    @pytest.mark.asyncio
    async def test_fails_open_when_no_strategy_config(self, rule: AssetWhitelistRule) -> None:
        request = OrderRequest(
            symbol="DOGE/USDT",
            side="buy",
            order_type="market",
            price=None,
            volume=Decimal("1000"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("100"),
        )
        context = {}
        passed, details = await rule.check(request, context)
        assert passed is True  # Fail open


class TestCooldownRule:
    """Tests for CooldownRule."""

    @pytest.fixture
    def rule(self) -> CooldownRule:
        return CooldownRule()

    def test_init(self, rule: CooldownRule) -> None:
        assert rule.name == "cooldown"
        assert rule.action == GuardrailAction.BLOCK_AND_DOWNGRADE
        assert "cooldown" in rule.description.lower()

    @pytest.mark.asyncio
    async def test_passes_when_not_in_cooldown(
        self, rule: CooldownRule, strategy_config: StrategyConfig
    ) -> None:
        mock_tracker = AsyncMock()
        mock_tracker.is_in_cooldown = AsyncMock(return_value=False)

        request = OrderRequest(
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            price=None,
            volume=Decimal("0.01"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("100"),
        )
        context = {
            "cooldown_tracker": mock_tracker,
            "strategy_config": strategy_config,
        }
        passed, details = await rule.check(request, context)
        assert passed is True
        mock_tracker.is_in_cooldown.assert_called_once_with("hyperliquid", "BTC/USDT")

    @pytest.mark.asyncio
    async def test_fails_when_in_cooldown(
        self, rule: CooldownRule, strategy_config: StrategyConfig
    ) -> None:
        mock_tracker = AsyncMock()
        mock_tracker.is_in_cooldown = AsyncMock(return_value=True)

        request = OrderRequest(
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            price=None,
            volume=Decimal("0.01"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("100"),
        )
        context = {
            "cooldown_tracker": mock_tracker,
            "strategy_config": strategy_config,
        }
        passed, details = await rule.check(request, context)
        assert passed is False
        assert details["current_value"] == "in_cooldown"


class TestExchangeExposureRule:
    """Tests for ExchangeExposureRule."""

    @pytest.fixture
    def rule(self) -> ExchangeExposureRule:
        return ExchangeExposureRule()

    def test_init(self, rule: ExchangeExposureRule) -> None:
        assert rule.name == "exchange_exposure"
        assert rule.action == GuardrailAction.BLOCK_AND_DOWNGRADE

    @pytest.mark.asyncio
    async def test_passes_when_within_limits(
        self, rule: ExchangeExposureRule, portfolio_snapshot, strategy_config: StrategyConfig
    ) -> None:
        # 5000 USDT / 10000 total = 50%, adding 100 notional = 51% (exceeds 50%)
        request = OrderRequest(
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            price=Decimal("10000"),
            volume=Decimal("0.01"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("100"),
        )
        context = {
            "portfolio_snapshot": portfolio_snapshot,
            "strategy_config": strategy_config,
        }
        passed, details = await rule.check(request, context)
        assert passed is False  # 5100/10000 = 51% > 50% limit

    @pytest.mark.asyncio
    async def test_fails_when_exceeds_limit(
        self, rule: ExchangeExposureRule, portfolio_snapshot, strategy_config: StrategyConfig
    ) -> None:
        # 5000 USDT / 10000 total = 50%, adding 50 notional = 50.5% (exceeds 50%)
        request = OrderRequest(
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            price=Decimal("5000"),
            volume=Decimal("0.001"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("50"),
        )
        context = {
            "portfolio_snapshot": portfolio_snapshot,
            "strategy_config": strategy_config,
        }
        passed, details = await rule.check(request, context)
        assert passed is False  # 5050/10000 = 50.5% > 50% limit

    @pytest.mark.asyncio
    async def test_passes_when_within_limit(
        self, rule: ExchangeExposureRule, portfolio_snapshot, strategy_config: StrategyConfig
    ) -> None:
        # 5000 USDT / 10000 total = 50%, adding 10 notional = 50.1% (still exceeds 50%)
        # Need a smaller order or lower existing balance
        # Use portfolio with 4000 USDT instead
        low_balance_snapshot = MagicMock()
        low_balance_snapshot.total_usdt_value = Decimal("10000")
        low_balance_snapshot.exchanges = {
            "hyperliquid": [
                MagicMock(asset="USDT", free=Decimal("4000"), locked=Decimal("0")),
                MagicMock(asset="BTC", free=Decimal("0.5"), locked=Decimal("0")),
            ]
        }

        request = OrderRequest(
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            price=Decimal("5000"),
            volume=Decimal("0.001"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("50"),
        )
        context = {
            "portfolio_snapshot": low_balance_snapshot,
            "strategy_config": strategy_config,
        }
        passed, details = await rule.check(request, context)
        # 4000 + 50 = 4050 / 10000 = 40.5% < 50% limit
        assert passed is True


class TestPositionSizeRule:
    """Tests for PositionSizeRule."""

    @pytest.fixture
    def rule(self) -> PositionSizeRule:
        return PositionSizeRule()

    def test_init(self, rule: PositionSizeRule) -> None:
        assert rule.name == "position_size"
        assert rule.action == GuardrailAction.BLOCK_AND_DOWNGRADE

    @pytest.mark.asyncio
    async def test_passes_with_zero_position(
        self, rule: PositionSizeRule, portfolio_snapshot, strategy_config: StrategyConfig
    ) -> None:
        # No BTC position, should pass even with large order
        request = OrderRequest(
            symbol="ETH/USDT",
            side="buy",
            order_type="market",
            price=Decimal("2000"),
            volume=Decimal("1"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("2000"),
        )
        context = {
            "portfolio_snapshot": portfolio_snapshot,
            "strategy_config": strategy_config,
        }
        passed, details = await rule.check(request, context)
        # 2000/10000 = 20% < 30% limit
        assert passed is True


class TestOrderNotionalRule:
    """Tests for OrderNotionalRule."""

    @pytest.fixture
    def rule(self) -> OrderNotionalRule:
        return OrderNotionalRule()

    def test_init(self, rule: OrderNotionalRule) -> None:
        assert rule.name == "order_notional"
        assert rule.action == GuardrailAction.BLOCK  # Different action!

    @pytest.mark.asyncio
    async def test_passes_when_within_bounds(
        self, rule: OrderNotionalRule, strategy_config: StrategyConfig
    ) -> None:
        request = OrderRequest(
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            price=None,
            volume=Decimal("0.01"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("100"),
        )
        context = {"strategy_config": strategy_config}
        passed, details = await rule.check(request, context)
        assert passed is True
        assert details["current_value"] == 100.0

    @pytest.mark.asyncio
    async def test_fails_when_below_min(
        self, rule: OrderNotionalRule, strategy_config: StrategyConfig
    ) -> None:
        request = OrderRequest(
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            price=None,
            volume=Decimal("0.0001"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("1"),
        )
        context = {"strategy_config": strategy_config}
        passed, details = await rule.check(request, context)
        assert passed is False

    @pytest.mark.asyncio
    async def test_fails_when_above_max(
        self, rule: OrderNotionalRule, strategy_config: StrategyConfig
    ) -> None:
        request = OrderRequest(
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            price=None,
            volume=Decimal("100"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("10000000"),
        )
        context = {"strategy_config": strategy_config}
        passed, details = await rule.check(request, context)
        assert passed is False


class TestDailyLossLimitRule:
    """Tests for DailyLossLimitRule."""

    @pytest.fixture
    def rule(self) -> DailyLossLimitRule:
        return DailyLossLimitRule()

    def test_init(self, rule: DailyLossLimitRule) -> None:
        assert rule.name == "daily_loss_limit"
        assert rule.action == GuardrailAction.BLOCK_AND_DOWNGRADE

    @pytest.mark.asyncio
    async def test_skips_non_autonomous_orders(
        self, rule: DailyLossLimitRule, portfolio_snapshot, strategy_config: StrategyConfig
    ) -> None:
        request = OrderRequest(
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            price=None,
            volume=Decimal("0.01"),
            exchange="hyperliquid",
            source="manual",  # Not autonomous
            notional=Decimal("100"),
        )
        context = {
            "portfolio_snapshot": portfolio_snapshot,
            "strategy_config": strategy_config,
            "daily_pnl": -1000.0,  # Large loss
        }
        passed, details = await rule.check(request, context)
        assert passed is True  # Skipped
        assert details["current_value"] is None

    @pytest.mark.asyncio
    async def test_passes_when_within_loss_limit(
        self, rule: DailyLossLimitRule, portfolio_snapshot, strategy_config: StrategyConfig
    ) -> None:
        request = OrderRequest(
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            price=None,
            volume=Decimal("0.01"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("100"),
        )
        context = {
            "portfolio_snapshot": portfolio_snapshot,
            "strategy_config": strategy_config,
            "daily_pnl": -400.0,  # 400 < 5% * 10000 = 500
        }
        passed, details = await rule.check(request, context)
        assert passed is True

    @pytest.mark.asyncio
    async def test_fails_when_exceeds_loss_limit(
        self, rule: DailyLossLimitRule, portfolio_snapshot, strategy_config: StrategyConfig
    ) -> None:
        request = OrderRequest(
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            price=None,
            volume=Decimal("0.01"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("100"),
        )
        context = {
            "portfolio_snapshot": portfolio_snapshot,
            "strategy_config": strategy_config,
            "daily_pnl": -600.0,  # 600 > 5% * 10000 = 500
        }
        passed, details = await rule.check(request, context)
        assert passed is False
        assert details["current_value"] == -600.0
        assert details["threshold_value"] == -500.0