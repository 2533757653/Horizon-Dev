"""Tests for OrderManager guardrail integration."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from horizon.internal.ordermanager.manager import (
    GuardrailBlockedError,
    OrderManager,
    OrderSubmissionError,
)
from horizon.internal.guardrails.evaluator import GuardrailResult, SystemMode
from horizon.internal.guardrails.rules import OrderRequest as GuardrailOrderRequest


def create_guardrail_result(passed=True, violated_rules=None, blocking=False, downgrade_autonomy=False, reason=""):
    """Create a GuardrailResult for testing."""
    if violated_rules is None:
        violated_rules = []
    return GuardrailResult(
        passed=passed,
        violated_rules=violated_rules,
        blocking=blocking,
        downgrade_autonomy=downgrade_autonomy,
        reason=reason or ("All guardrail rules passed" if passed else f"Blocked by: {', '.join(violated_rules)}"),
    )


class TestGuardrailBlockedError:
    """Tests for GuardrailBlockedError exception."""

    def test_str_returns_formatted_message(self) -> None:
        """Test __str__ returns correct format."""
        result = create_guardrail_result(passed=False, violated_rules=["cooldown"], reason="In cooldown")
        error = GuardrailBlockedError("In cooldown", result)
        assert str(error) == "Guardrail blocked: In cooldown"

    def test_reason_attribute_accessible(self) -> None:
        """Test reason attribute is accessible."""
        result = create_guardrail_result(reason="Test reason")
        error = GuardrailBlockedError("Test reason", result)
        assert error.reason == "Test reason"

    def test_result_attribute_contains_full_result(self) -> None:
        """Test result attribute contains full GuardrailResult."""
        result = create_guardrail_result(
            passed=False,
            violated_rules=["asset_whitelist", "cooldown"],
            blocking=True,
            downgrade_autonomy=True,
            reason="Multiple violations",
        )
        error = GuardrailBlockedError("Multiple violations", result)
        assert error.result.passed is False
        assert "asset_whitelist" in error.result.violated_rules
        assert "cooldown" in error.result.violated_rules
        assert error.result.blocking is True
        assert error.result.downgrade_autonomy is True


class TestOrderManagerGuardrailIntegration:
    """Tests for OrderManager with guardrail integration."""

    @pytest.fixture
    def mock_registry(self):
        """Create mock exchange registry."""
        registry = MagicMock()
        adapter = MagicMock()
        adapter.enabled = True
        adapter.place_market_order = AsyncMock(return_value=MagicMock(
            order_id="exchange-order-123",
            price=Decimal("50000"),
            volume=Decimal("0.01"),
            filled_volume=Decimal("0"),
            status="submitted",
        ))
        adapter.place_limit_order = AsyncMock(return_value=MagicMock(
            order_id="exchange-order-123",
            price=Decimal("50000"),
            volume=Decimal("0.01"),
            filled_volume=Decimal("0"),
            status="submitted",
        ))
        registry.get = MagicMock(return_value=adapter)
        return registry

    @pytest.fixture
    def mock_db(self):
        """Create mock database connection."""
        db = MagicMock()
        db.execute = AsyncMock()
        db.commit = AsyncMock()
        return db

    @pytest.fixture
    def order_request(self):
        """Create standard order request."""
        return OrderManager.OrderRequest(
            exchange="hyperliquid",
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            price=Decimal("50000"),
            volume=Decimal("0.01"),
            source="autonomous",
        )

    @pytest.mark.asyncio
    async def test_submit_order_calls_guardrail_evaluator(self, mock_registry, mock_db, order_request) -> None:
        """Test submit_order calls guardrail evaluator when configured."""
        mock_evaluator = MagicMock()
        mock_evaluator.evaluate = AsyncMock(return_value=create_guardrail_result(passed=True))

        mock_cooldown = MagicMock()
        mock_cooldown.record_trade = AsyncMock()

        mock_strategy_config = MagicMock()
        mock_strategy_config.cooldown_seconds = 300

        manager = OrderManager(
            registry=mock_registry,
            db=mock_db,
            active_exchange="hyperliquid",
            guardrail_evaluator=mock_evaluator,
            cooldown_tracker=mock_cooldown,
            strategy_config=mock_strategy_config,
        )

        await manager.submit_order(order_request)

        mock_evaluator.evaluate.assert_called_once()
        call_args = mock_evaluator.evaluate.call_args[0][0]
        assert call_args.symbol == "BTC/USDT"
        assert call_args.side == "buy"
        assert call_args.exchange == "hyperliquid"

    @pytest.mark.asyncio
    async def test_submit_order_blocks_on_guardrail_failure(self, mock_registry, mock_db, order_request) -> None:
        """Test submit_order raises GuardrailBlockedError when guardrail fails."""
        mock_evaluator = MagicMock()
        mock_evaluator.evaluate = AsyncMock(return_value=create_guardrail_result(
            passed=False,
            violated_rules=["cooldown"],
            blocking=True,
            reason="Cooldown period active",
        ))

        manager = OrderManager(
            registry=mock_registry,
            db=mock_db,
            active_exchange="hyperliquid",
            guardrail_evaluator=mock_evaluator,
        )

        with pytest.raises(GuardrailBlockedError) as exc_info:
            await manager.submit_order(order_request)

        assert "Cooldown period active" in str(exc_info.value)
        assert exc_info.value.result.violated_rules == ["cooldown"]

    @pytest.mark.asyncio
    async def test_submit_order_records_guardrail_blocked_event(self, mock_registry, mock_db, order_request) -> None:
        """Test submit_order records guardrail_blocked event when blocked."""
        mock_evaluator = MagicMock()
        mock_evaluator.evaluate = AsyncMock(return_value=create_guardrail_result(
            passed=False,
            violated_rules=["asset_whitelist"],
            blocking=True,
            downgrade_autonomy=True,
            reason="Asset not in whitelist",
        ))

        manager = OrderManager(
            registry=mock_registry,
            db=mock_db,
            active_exchange="hyperliquid",
            guardrail_evaluator=mock_evaluator,
        )

        # Record the current call count for record_event
        initial_call_count = mock_db.execute.call_count

        with pytest.raises(GuardrailBlockedError):
            await manager.submit_order(order_request)

        # Find the guardrail_blocked event call
        calls = mock_db.execute.call_args_list
        guardrail_blocked_call = None
        for call in calls:
            if "INSERT INTO order_events" in call[0][0]:
                # Check if this is the guardrail_blocked event
                args = call[0][1]
                if args[1] == "guardrail_blocked":
                    guardrail_blocked_call = call
                    break

        assert guardrail_blocked_call is not None, "guardrail_blocked event not recorded"

    @pytest.mark.asyncio
    async def test_submit_order_logs_warnings_when_passing_with_violations(self, mock_registry, mock_db, order_request) -> None:
        """Test submit_order logs warnings when rules pass with violations (alerts)."""
        mock_evaluator = MagicMock()
        # Passed but with alerts/warnings
        mock_evaluator.evaluate = AsyncMock(return_value=create_guardrail_result(
            passed=True,
            violated_rules=["daily_loss_limit"],  # Alert but not blocking
            blocking=False,
            reason="Alert: daily loss limit approaching",
        ))

        manager = OrderManager(
            registry=mock_registry,
            db=mock_db,
            active_exchange="hyperliquid",
            guardrail_evaluator=mock_evaluator,
        )

        with patch("horizon.internal.ordermanager.manager.logger") as mock_logger:
            await manager.submit_order(order_request)
            # Warning should be logged for non-blocking violations
            mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_submit_order_skips_guardrail_when_not_configured(self, mock_registry, mock_db, order_request) -> None:
        """Test submit_order skips guardrail evaluation when evaluator is None."""
        mock_evaluator = MagicMock()
        mock_evaluator.evaluate = AsyncMock(return_value=create_guardrail_result(passed=True))

        manager = OrderManager(
            registry=mock_registry,
            db=mock_db,
            active_exchange="hyperliquid",
            guardrail_evaluator=None,  # No evaluator
        )

        await manager.submit_order(order_request)

        # evaluate should not be called
        mock_evaluator.evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_submit_order_records_cooldown_on_success(self, mock_registry, mock_db, order_request) -> None:
        """Test submit_order records cooldown after successful order."""
        mock_evaluator = MagicMock()
        mock_evaluator.evaluate = AsyncMock(return_value=create_guardrail_result(passed=True))

        mock_cooldown = MagicMock()
        mock_cooldown.record_trade = AsyncMock()

        mock_strategy_config = MagicMock()
        mock_strategy_config.cooldown_seconds = 300

        manager = OrderManager(
            registry=mock_registry,
            db=mock_db,
            active_exchange="hyperliquid",
            guardrail_evaluator=mock_evaluator,
            cooldown_tracker=mock_cooldown,
            strategy_config=mock_strategy_config,
        )

        await manager.submit_order(order_request)

        mock_cooldown.record_trade.assert_called_once_with("hyperliquid", "BTC/USDT", 300)

    @pytest.mark.asyncio
    async def test_submit_order_no_cooldown_when_tracker_none(self, mock_registry, mock_db, order_request) -> None:
        """Test submit_order works without cooldown_tracker (backward compatibility)."""
        mock_evaluator = MagicMock()
        mock_evaluator.evaluate = AsyncMock(return_value=create_guardrail_result(passed=True))

        mock_cooldown = MagicMock()
        mock_cooldown.record_trade = AsyncMock()

        manager = OrderManager(
            registry=mock_registry,
            db=mock_db,
            active_exchange="hyperliquid",
            guardrail_evaluator=mock_evaluator,
            cooldown_tracker=None,  # No cooldown tracker
            strategy_config=None,
        )

        # Should not raise
        await manager.submit_order(order_request)

        # record_trade should not be called
        mock_cooldown.record_trade.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_guardrail_status_returns_correct_structure(self, mock_registry, mock_db) -> None:
        """Test get_guardrail_status returns mode, cooldowns, and events count."""
        mock_evaluator = MagicMock()
        mock_evaluator.get_current_mode = AsyncMock(return_value=SystemMode.PAPER)

        mock_cooldown = MagicMock()
        mock_cooldown.get_all_cooldowns = AsyncMock(return_value=[
            {"exchange": "hyperliquid", "symbol": "BTC/USDT", "remaining_seconds": 150},
        ])

        # Mock the query for guardrail events count
        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value={"count": 5})

        async def mock_execute(*args, **kwargs):
            return mock_cursor

        mock_db.execute = mock_execute

        manager = OrderManager(
            registry=mock_registry,
            db=mock_db,
            active_exchange="hyperliquid",
            guardrail_evaluator=mock_evaluator,
            cooldown_tracker=mock_cooldown,
        )

        status = await manager.get_guardrail_status()

        assert status["mode"] == "paper"
        assert len(status["cooldowns"]) == 1
        assert status["guardrail_events_today"] == 5

    @pytest.mark.asyncio
    async def test_get_guardrail_status_defaults_when_no_evaluator(self, mock_registry, mock_db) -> None:
        """Test get_guardrail_status defaults to LIVE when no evaluator."""
        mock_cooldown = MagicMock()
        mock_cooldown.get_all_cooldowns = AsyncMock(return_value=[])

        mock_cursor = MagicMock()
        mock_cursor.fetchone = AsyncMock(return_value={"count": 0})

        async def mock_execute(*args, **kwargs):
            return mock_cursor

        mock_db.execute = mock_execute

        manager = OrderManager(
            registry=mock_registry,
            db=mock_db,
            active_exchange="hyperliquid",
            guardrail_evaluator=None,
            cooldown_tracker=mock_cooldown,
        )

        status = await manager.get_guardrail_status()

        assert status["mode"] == "live"
        assert status["cooldowns"] == []
        assert status["guardrail_events_today"] == 0

    def test_guardrail_blocked_error_inheritance(self) -> None:
        """Test GuardrailBlockedError is an Exception subclass."""
        result = create_guardrail_result(reason="Test")
        error = GuardrailBlockedError("Test", result)
        assert isinstance(error, Exception)