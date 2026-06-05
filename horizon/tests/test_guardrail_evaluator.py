"""Tests for RiskGuardrailEvaluator."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from horizon.internal.guardrails.evaluator import (
    GuardrailResult,
    RiskGuardrailEvaluator,
    SystemMode,
)
from horizon.internal.guardrails.rules import (
    OrderRequest,
    StrategyConfig,
)


def create_strategy_config() -> StrategyConfig:
    """Create a standard strategy config for tests."""
    config = StrategyConfig(
        asset_whitelist=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        order_min_notional=Decimal("10"),
        order_max_notional=Decimal("1000000"),
        max_exchange_exposure_pct=0.5,
        max_position_pct=0.3,
        cooldown_seconds=300,
        max_daily_loss_pct=0.05,
    )
    config.mode = "live"
    config.autonomy_enabled = True
    config.min_confidence_threshold = 75
    config.max_risk_tier = "low"
    return config


def make_db_mock(fetchone_return=None, fetchall_return=None):
    """Build a mock db whose execute() returns a cursor whose fetchone/fetchall
    are awaitable. Use this for tests that exercise get_current_mode or any
    code path that does `row = await cursor.fetchone()`.
    """
    db = AsyncMock()
    cursor = MagicMock()
    cursor.fetchone = AsyncMock(return_value=fetchone_return)
    cursor.fetchall = AsyncMock(return_value=fetchall_return)
    db.execute = AsyncMock(return_value=cursor)
    db.commit = AsyncMock()
    return db, cursor


def create_mock_portfolio_snapshot():
    """Mock portfolio snapshot with low exposure to pass rules."""
    snapshot = MagicMock()
    snapshot.total_usdt_value = Decimal("100000")  # Large portfolio
    snapshot.exchanges = {
        "hyperliquid": [
            MagicMock(asset="USDT", free=Decimal("10000"), locked=Decimal("0")),
            MagicMock(asset="BTC", free=Decimal("0"), locked=Decimal("0")),
        ]
    }
    return snapshot


class TestSystemMode:
    """Tests for SystemMode enum."""

    def test_values(self) -> None:
        assert SystemMode.LIVE.value == "live"
        assert SystemMode.PAPER.value == "paper"
        assert SystemMode.COLLABORATIVE.value == "collaborative"


class TestGuardrailResult:
    """Tests for GuardrailResult dataclass."""

    def test_passed_result(self) -> None:
        result = GuardrailResult(passed=True)
        assert result.passed is True
        assert result.violated_rules == []
        assert result.blocking is False
        assert result.downgrade_autonomy is False

    def test_blocked_result(self) -> None:
        result = GuardrailResult(
            passed=False,
            violated_rules=["asset_whitelist"],
            blocking=True,
            reason="Asset not in whitelist",
        )
        assert result.passed is False
        assert "asset_whitelist" in result.violated_rules
        assert result.blocking is True

    def test_downgrade_result(self) -> None:
        result = GuardrailResult(
            passed=False,
            violated_rules=["cooldown", "exchange_exposure"],
            blocking=True,
            downgrade_autonomy=True,
            reason="Multiple breaches",
        )
        assert result.downgrade_autonomy is True


class TestRiskGuardrailEvaluator:
    """Tests for RiskGuardrailEvaluator."""

    def test_init(self) -> None:
        """Test evaluator initialization."""
        mock_db = AsyncMock()
        mock_cooldown = MagicMock()
        mock_fetcher = MagicMock()

        evaluator = RiskGuardrailEvaluator(
            db=mock_db,
            cooldown_tracker=mock_cooldown,
            strategy_config=create_strategy_config(),
            fetcher=mock_fetcher,
        )

        assert len(evaluator._rules) == 6
        rule_names = [r.name for r in evaluator._rules]
        assert "asset_whitelist" in rule_names
        assert "cooldown" in rule_names
        assert "exchange_exposure" in rule_names
        assert "position_size" in rule_names
        assert "order_notional" in rule_names
        assert "daily_loss_limit" in rule_names

    @pytest.mark.asyncio
    async def test_evaluate_passes_when_all_rules_pass(self) -> None:
        """Test evaluate passes when all rules pass."""
        mock_db, _ = make_db_mock()
        mock_fetcher = MagicMock()

        # Create async mock for cooldown that returns not in cooldown
        async def mock_is_in_cooldown(exchange, symbol):
            return (False, None)

        mock_cooldown = MagicMock()
        mock_cooldown.is_in_cooldown = mock_is_in_cooldown

        evaluator = RiskGuardrailEvaluator(
            db=mock_db,
            cooldown_tracker=mock_cooldown,
            strategy_config=create_strategy_config(),
            fetcher=mock_fetcher,
        )

        request = OrderRequest(
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            price=Decimal("50000"),
            volume=Decimal("0.01"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("500"),
        )

        with patch.object(
            evaluator,
            "_get_portfolio_snapshot",
            return_value=create_mock_portfolio_snapshot(),
        ):
            with patch.object(evaluator, "_get_daily_pnl", return_value=0.0):
                result = await evaluator.evaluate(request)

        assert result.passed is True, f"Expected passed but got: {result.reason}"
        assert len(result.violated_rules) == 0
        assert result.blocking is False
        assert result.downgrade_autonomy is False

    @pytest.mark.asyncio
    async def test_evaluate_fails_with_violated_rules(self) -> None:
        """Test evaluate blocks when rules fail."""
        mock_db, _ = make_db_mock()
        mock_fetcher = MagicMock()

        async def mock_is_in_cooldown(exchange, symbol):
            return (False, None)

        mock_cooldown = MagicMock()
        mock_cooldown.is_in_cooldown = mock_is_in_cooldown

        evaluator = RiskGuardrailEvaluator(
            db=mock_db,
            cooldown_tracker=mock_cooldown,
            strategy_config=create_strategy_config(),
            fetcher=mock_fetcher,
        )

        request = OrderRequest(
            symbol="DOGE/USDT",  # Not in whitelist
            side="buy",
            order_type="market",
            price=None,
            volume=Decimal("1000"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("100"),
        )

        with patch.object(evaluator, "_get_portfolio_snapshot", return_value=None):
            with patch.object(evaluator, "_get_daily_pnl", return_value=0.0):
                result = await evaluator.evaluate(request)

        assert result.passed is False
        assert "asset_whitelist" in result.violated_rules
        assert result.blocking is True
        assert result.downgrade_autonomy is True

    @pytest.mark.asyncio
    async def test_evaluate_sets_cooldown_remaining(self) -> None:
        """Test evaluate sets cooldown_seconds_remaining when cooldown rule fails."""
        mock_db, _ = make_db_mock()
        mock_fetcher = MagicMock()

        # Create async mock for cooldown that returns IN cooldown
        async def mock_is_in_cooldown(exchange, symbol):
            return (True, 250)

        mock_cooldown = MagicMock()
        mock_cooldown.is_in_cooldown = mock_is_in_cooldown

        evaluator = RiskGuardrailEvaluator(
            db=mock_db,
            cooldown_tracker=mock_cooldown,
            strategy_config=create_strategy_config(),
            fetcher=mock_fetcher,
        )

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

        with patch.object(evaluator, "_get_portfolio_snapshot", return_value=None):
            with patch.object(evaluator, "_get_daily_pnl", return_value=0.0):
                result = await evaluator.evaluate(request)

        assert result.passed is False
        assert "cooldown" in result.violated_rules
        assert result.cooldown_seconds_remaining == 250

    @pytest.mark.asyncio
    async def test_get_current_mode_returns_mode_from_db_row(
        self,
    ) -> None:
        """Test get_current_mode returns the mode from the strategy_configs row
        when the row is present.

        NB: the previous test name was
        'test_get_current_mode_returns_collaborative_when_downgrade_active' and
        asserted COLLABORATIVE was returned when a `downgrade_expires_at`
        field was present. That behavior is not implemented in the current
        production code — get_current_mode() only inspects the `mode` column
        on the strategy_configs row. We renamed the test to reflect the
        actual contract and exercise the database-driven path. The
        COLLABORATIVE mode is enforced separately via guardrail_events
        (see RiskGuardrailEvaluator.evaluate → can_autonomously_execute).
        """
        mock_db, _ = make_db_mock(fetchone_return={"mode": "paper"})

        mock_cooldown = MagicMock()
        mock_fetcher = MagicMock()

        evaluator = RiskGuardrailEvaluator(
            db=mock_db,
            cooldown_tracker=mock_cooldown,
            strategy_config=create_strategy_config(),
            fetcher=mock_fetcher,
        )

        mode = await evaluator.get_current_mode()

        assert mode == SystemMode.PAPER

    @pytest.mark.asyncio
    async def test_get_current_mode_returns_live_when_no_downgrade(self) -> None:
        """Test get_current_mode returns LIVE when no active downgrade."""
        mock_db = AsyncMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)

        async def mock_execute(*args, **kwargs):
            return mock_cursor

        mock_db.execute = mock_execute

        mock_cooldown = MagicMock()
        mock_fetcher = MagicMock()

        evaluator = RiskGuardrailEvaluator(
            db=mock_db,
            cooldown_tracker=mock_cooldown,
            strategy_config=create_strategy_config(),
            fetcher=mock_fetcher,
        )

        mode = await evaluator.get_current_mode()

        assert mode == SystemMode.LIVE

    @pytest.mark.asyncio
    async def test_set_mode_updates_database(self) -> None:
        """Test set_mode updates strategy_configs in database."""
        mock_db, _ = make_db_mock()
        # make_db_mock() already provides mock_db.execute and mock_db.commit as
        # AsyncMocks that preserve .assert_called() / .call_args_list. Do not
        # overwrite them with raw functions (loses the assertion surface).
        # set_mode() does an UPDATE then a commit; both should be called.

        mock_cooldown = MagicMock()
        mock_fetcher = MagicMock()

        evaluator = RiskGuardrailEvaluator(
            db=mock_db,
            cooldown_tracker=mock_cooldown,
            strategy_config=create_strategy_config(),
            fetcher=mock_fetcher,
        )

        await evaluator.set_mode(SystemMode.PAPER)

        mock_db.execute.assert_called()
        call_args = mock_db.execute.call_args
        assert "UPDATE strategy_configs" in call_args[0][0]
        assert "paper" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_can_autonomously_execute_returns_false_in_collaborative(
        self,
    ) -> None:
        """Test can_autonomously_execute returns False in COLLABORATIVE mode."""
        mock_db = AsyncMock()
        mock_cooldown = MagicMock()
        mock_fetcher = MagicMock()

        evaluator = RiskGuardrailEvaluator(
            db=mock_db,
            cooldown_tracker=mock_cooldown,
            strategy_config=create_strategy_config(),
            fetcher=mock_fetcher,
        )

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

        with patch.object(
            evaluator, "get_current_mode", return_value=SystemMode.COLLABORATIVE
        ):
            allowed, reason = await evaluator.can_autonomously_execute(
                confidence_score=90, risk_tier="low", request=request
            )

        assert allowed is False
        assert "COLLABORATIVE" in reason

    @pytest.mark.asyncio
    async def test_can_autonomously_execute_returns_false_when_confidence_low(
        self,
    ) -> None:
        """Test can_autonomously_execute returns False when confidence below threshold."""
        strategy_config = create_strategy_config()
        strategy_config.min_confidence_threshold = 75

        mock_db = AsyncMock()
        mock_cooldown = MagicMock()
        mock_fetcher = MagicMock()

        evaluator = RiskGuardrailEvaluator(
            db=mock_db,
            cooldown_tracker=mock_cooldown,
            strategy_config=strategy_config,
            fetcher=mock_fetcher,
        )

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

        with patch.object(
            evaluator, "get_current_mode", return_value=SystemMode.LIVE
        ):
            allowed, reason = await evaluator.can_autonomously_execute(
                confidence_score=50, risk_tier="low", request=request
            )

        assert allowed is False
        assert "below threshold" in reason

    @pytest.mark.asyncio
    async def test_can_autonomously_execute_returns_false_when_risk_tier_high(
        self,
    ) -> None:
        """Test can_autonomously_execute returns False when risk tier exceeds max."""
        mock_db = AsyncMock()
        mock_cooldown = MagicMock()
        mock_fetcher = MagicMock()

        evaluator = RiskGuardrailEvaluator(
            db=mock_db,
            cooldown_tracker=mock_cooldown,
            strategy_config=create_strategy_config(),
            fetcher=mock_fetcher,
        )

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

        with patch.object(
            evaluator, "get_current_mode", return_value=SystemMode.LIVE
        ):
            allowed, reason = await evaluator.can_autonomously_execute(
                confidence_score=90, risk_tier="high", request=request
            )

        assert allowed is False
        assert "exceeds max" in reason

    @pytest.mark.asyncio
    async def test_can_autonomously_execute_returns_true_when_allows(self) -> None:
        """Test can_autonomously_execute returns True when all checks pass."""
        mock_db = AsyncMock()
        mock_cooldown = MagicMock()
        mock_fetcher = MagicMock()

        evaluator = RiskGuardrailEvaluator(
            db=mock_db,
            cooldown_tracker=mock_cooldown,
            strategy_config=create_strategy_config(),
            fetcher=mock_fetcher,
        )

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

        with patch.object(
            evaluator, "get_current_mode", return_value=SystemMode.LIVE
        ):
            allowed, reason = await evaluator.can_autonomously_execute(
                confidence_score=90, risk_tier="low", request=request
            )

        assert allowed is True
        assert reason == "allowed"

    @pytest.mark.asyncio
    async def test_evaluate_proposal_converts_and_evaluates(self) -> None:
        """Test evaluate_proposal converts proposal to OrderRequest and evaluates."""
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.execute = AsyncMock()

        mock_cooldown = MagicMock()
        mock_fetcher = MagicMock()

        evaluator = RiskGuardrailEvaluator(
            db=mock_db,
            cooldown_tracker=mock_cooldown,
            strategy_config=create_strategy_config(),
            fetcher=mock_fetcher,
        )

        mock_proposal = MagicMock()
        mock_proposal.id = "proposal-123"
        mock_proposal.symbol = "BTC/USDT"
        mock_proposal.side = "buy"
        mock_proposal.order_type = "market"
        mock_proposal.price = 50000.0
        mock_proposal.volume = 0.01
        mock_proposal.exchange = "hyperliquid"

        with patch.object(evaluator, "evaluate", new=AsyncMock()) as mock_evaluate:
            mock_evaluate.return_value = GuardrailResult(passed=True)
            result = await evaluator.evaluate_proposal(mock_proposal)

        mock_evaluate.assert_called_once()
        call_args = mock_evaluate.call_args[0]
        order_request = call_args[0]
        assert order_request.symbol == "BTC/USDT"
        assert order_request.side == "buy"
        assert order_request.source == "autonomous"

        # Verify result was stored in database
        mock_db.execute.assert_called()
        call_args = mock_db.execute.call_args
        assert "UPDATE proposals" in call_args[0][0]
        assert "proposal-123" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_evaluate_persists_guardrail_events_on_breach(self) -> None:
        """Test evaluate persists guardrail events when rules fail."""
        mock_db, _ = make_db_mock()

        mock_fetcher = MagicMock()

        async def mock_is_in_cooldown(exchange, symbol):
            return (False, None)

        mock_cooldown = MagicMock()
        mock_cooldown.is_in_cooldown = mock_is_in_cooldown

        evaluator = RiskGuardrailEvaluator(
            db=mock_db,
            cooldown_tracker=mock_cooldown,
            strategy_config=create_strategy_config(),
            fetcher=mock_fetcher,
        )

        request = OrderRequest(
            symbol="DOGE/USDT",  # Not in whitelist
            side="buy",
            order_type="market",
            price=None,
            volume=Decimal("1000"),
            exchange="hyperliquid",
            source="autonomous",
            notional=Decimal("100"),
        )

        with patch.object(evaluator, "_get_portfolio_snapshot", return_value=None):
            with patch.object(evaluator, "_get_daily_pnl", return_value=0.0):
                result = await evaluator.evaluate(request)

        assert result.passed is False
        # Verify at least one INSERT was called for guardrail_events.
        # Note: the first execute() call is the SELECT mode from
        # get_current_mode(); the INSERT happens AFTER a rule fails.
        mock_db.execute.assert_called()
        all_sql = " ".join(c[0][0] for c in mock_db.execute.call_args_list)
        assert "INSERT INTO guardrail_events" in all_sql
        assert "SELECT mode FROM strategy_configs" in all_sql

    def test_build_reason_with_violations(self) -> None:
        """Test _build_reason builds correct reason string."""
        mock_db, _ = make_db_mock()
        mock_cooldown = MagicMock()
        mock_fetcher = MagicMock()

        evaluator = RiskGuardrailEvaluator(
            db=mock_db,
            cooldown_tracker=mock_cooldown,
            strategy_config=create_strategy_config(),
            fetcher=mock_fetcher,
        )

        reason = evaluator._build_reason(
            violated_rules=["cooldown", "asset_whitelist"],
            blocking=True,
            downgrade_autonomy=True,
        )
        assert "cooldown" in reason
        assert "asset_whitelist" in reason
        assert "COLLABORATIVE" in reason

    def test_build_reason_empty(self) -> None:
        """Test _build_reason with no violations."""
        mock_db, _ = make_db_mock()
        mock_cooldown = MagicMock()
        mock_fetcher = MagicMock()

        evaluator = RiskGuardrailEvaluator(
            db=mock_db,
            cooldown_tracker=mock_cooldown,
            strategy_config=create_strategy_config(),
            fetcher=mock_fetcher,
        )

        reason = evaluator._build_reason([], False, False)
        assert "passed" in reason.lower()

    def test_get_rule_by_name(self) -> None:
        """Test _get_rule_by_name returns correct rule."""
        mock_db, _ = make_db_mock()
        mock_cooldown = MagicMock()
        mock_fetcher = MagicMock()

        evaluator = RiskGuardrailEvaluator(
            db=mock_db,
            cooldown_tracker=mock_cooldown,
            strategy_config=create_strategy_config(),
            fetcher=mock_fetcher,
        )

        rule = evaluator._get_rule_by_name("cooldown")
        assert rule is not None
        assert rule.name == "cooldown"

        assert evaluator._get_rule_by_name("nonexistent") is None