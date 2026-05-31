"""Tests for LLM Prompt Builder."""

import json
import pytest

from horizon.internal.llm import MarketContext, PortfolioContext, PromptBuilder


class TestPromptBuilder:
    """Unit tests for PromptBuilder."""

    @pytest.fixture
    def strategy_config(self):
        """Sample strategy configuration."""
        return {
            "id": 1,
            "name": "default_long_term",
            "enabled": 1,
            "mode": "live",
            "autonomy_enabled": 0,
            "min_confidence_threshold": 75,
            "max_risk_tier": "low",
            "analysis_interval_hours": 8,
            "asset_whitelist": '["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]',
            "max_position_pct": 20.0,
            "max_daily_loss_pct": 5.0,
            "max_exchange_exposure_pct": 50.0,
            "cooldown_seconds": 300,
        }

    @pytest.fixture
    def market_context(self):
        """Sample market context."""
        return MarketContext(
            tickers=[
                {
                    "symbol": "BTCUSDT",
                    "price": "65000.00",
                    "volume_24h": "1234567890.00",
                    "bid": "64999.00",
                    "ask": "65001.00",
                },
                {
                    "symbol": "ETHUSDT",
                    "price": "3500.00",
                    "volume_24h": "987654321.00",
                    "bid": "3499.00",
                    "ask": "3501.00",
                },
            ],
            technical_indicators={
                "BTCUSDT": {
                    "rsi_14": 65.5,
                    "macd_line": 150.25,
                    "macd_signal": 120.30,
                    "macd_histogram": 29.95,
                    "ema_20": 64000.00,
                    "ema_50": 63000.00,
                    "bollinger_upper": 67000.00,
                    "bollinger_lower": 62000.00,
                    "atr_14": 850.00,
                },
                "ETHUSDT": {
                    "rsi_14": 58.3,
                    "macd_line": 45.20,
                    "macd_signal": 40.15,
                    "macd_histogram": 5.05,
                    "ema_20": 3450.00,
                    "ema_50": 3400.00,
                    "bollinger_upper": 3600.00,
                    "bollinger_lower": 3300.00,
                    "atr_14": 75.00,
                },
            },
            orderbooks={},
            recent_trades={},
        )

    @pytest.fixture
    def portfolio_context(self):
        """Sample portfolio context."""
        return PortfolioContext(
            balances=[
                {
                    "asset": "USDT",
                    "free": "10000.00",
                    "locked": "0.00",
                    "exchange": "binance",
                },
                {
                    "asset": "BTC",
                    "free": "0.50",
                    "locked": "0.10",
                    "exchange": "binance",
                },
                {
                    "asset": "ETH",
                    "free": "5.00",
                    "locked": "0.00",
                    "exchange": "binance",
                },
            ],
            total_usdt_value="50000.00",
            open_positions={
                "BTCUSDT": {
                    "side": "buy",
                    "volume": "0.50",
                    "entry_price": "60000.00",
                },
            },
        )

    @pytest.fixture
    def builder(self, strategy_config):
        """Create PromptBuilder instance."""
        return PromptBuilder(strategy_config)

    def test_build_returns_non_empty_string(self, builder, market_context, portfolio_context, strategy_config):
        """Test that build() returns a non-empty string."""
        result = builder.build(market_context, portfolio_context, strategy_config)
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    def test_all_7_sections_present(self, builder, market_context, portfolio_context, strategy_config):
        """Test that all 7 required sections are present in the prompt."""
        result = builder.build(market_context, portfolio_context, strategy_config)

        # Section 1: System persona
        assert "You are Horizon" in result
        assert "quantitative trading analyst" in result

        # Section 2: Current market snapshot
        assert "## Current Market Snapshot" in result or "## Current Market" in result
        assert "BTCUSDT" in result
        assert "ETHUSDT" in result

        # Section 3: Portfolio state
        assert "## Portfolio State" in result
        assert "50000.00" in result  # total_usdt_value

        # Section 4: Strategy parameters
        assert "## Strategy Parameters" in result
        assert "Max Position" in result
        assert "Max Daily Loss" in result

        # Section 5: Instructions
        assert "## Instructions" in result
        assert "confidence_score" in result

        # Section 6: Response format
        assert "## Response Format" in result
        assert '"analysis_summary"' in result
        assert '"proposals"' in result
        assert '"exchange"' in result
        assert '"symbol"' in result
        assert '"side"' in result
        assert '"order_type"' in result
        assert '"price"' in result
        assert '"volume"' in result
        assert '"confidence_score"' in result
        assert '"risk_tier"' in result
        assert '"rationale"' in result

        # Section 7: Constraints reminder
        assert "## Constraints Reminder" in result
        assert "Confidence score must be honest" in result

    def test_exact_json_schema_included(self, builder, market_context, portfolio_context, strategy_config):
        """Test that the exact JSON schema is present in the response format section."""
        result = builder.build(market_context, portfolio_context, strategy_config)

        # Check for exact schema fields
        assert '"analysis_summary": "string' in result
        assert '"confidence_explanation": "string' in result
        assert '"proposals": [' in result
        assert '"exchange": "binance|htx|hyperliquid|bitget"' in result
        assert '"symbol": "BTCUSDT"' in result
        assert '"side": "buy|sell"' in result
        assert '"order_type": "market|limit"' in result
        assert '"price": "12345.67"' in result
        assert '"volume": "0.5"' in result
        assert '"confidence_score": 85' in result
        assert '"risk_tier": "low|medium|high"' in result
        assert '"rationale": "Detailed explanation of the trade thesis"' in result

    def test_asset_whitelist_mentioned(self, builder, market_context, portfolio_context, strategy_config):
        """Test that asset whitelist from strategy config is mentioned in prompt."""
        result = builder.build(market_context, portfolio_context, strategy_config)

        # Should mention all whitelist assets
        assert "BTCUSDT" in result
        assert "ETHUSDT" in result
        assert "SOLUSDT" in result
        assert "BNBUSDT" in result

    def test_determinism_same_inputs_same_output(self, builder, market_context, portfolio_context, strategy_config):
        """Test that given identical inputs, build() returns identical output."""
        result1 = builder.build(market_context, portfolio_context, strategy_config)
        result2 = builder.build(market_context, portfolio_context, strategy_config)
        result3 = builder.build(market_context, portfolio_context, strategy_config)

        assert result1 == result2 == result3

    def test_determinism_with_technical_indicators(self, strategy_config):
        """Test determinism with various technical indicator values."""
        builder = PromptBuilder(strategy_config)

        market_context = MarketContext(
            tickers=[
                {"symbol": "BTCUSDT", "price": "65000.00", "volume_24h": "1000.00", "bid": "64999.00", "ask": "65001.00"},
            ],
            technical_indicators={
                "BTCUSDT": {
                    "rsi_14": 65.5,
                    "macd_line": 150.25,
                    "macd_signal": 120.30,
                    "macd_histogram": 29.95,
                    "ema_20": 64000.00,
                    "ema_50": 63000.00,
                    "bollinger_upper": 67000.00,
                    "bollinger_lower": 62000.00,
                    "atr_14": 850.00,
                },
            },
        )

        portfolio_context = PortfolioContext(
            balances=[],
            total_usdt_value="10000.00",
            open_positions={},
        )

        result1 = builder.build(market_context, portfolio_context, strategy_config)
        result2 = builder.build(market_context, portfolio_context, strategy_config)

        assert result1 == result2

    def test_prompt_length_under_limit(self, builder, market_context, portfolio_context, strategy_config):
        """Test that prompt length is reasonable (under typical context window)."""
        result = builder.build(market_context, portfolio_context, strategy_config)

        # Typical Claude context window is 200K tokens, but we target under 150K chars
        # With a typical market context, prompt should be well under this
        assert len(result) < 150000, f"Prompt length {len(result)} exceeds 150000 characters"

    def test_empty_market_context(self, builder, strategy_config):
        """Test that prompt builds correctly with empty market context."""
        market_context = MarketContext()
        portfolio_context = PortfolioContext(
            balances=[],
            total_usdt_value="0",
            open_positions={},
        )

        result = builder.build(market_context, portfolio_context, strategy_config)

        assert len(result) > 0
        assert "## Current Market Snapshot" in result
        assert "## Portfolio State" in result
        assert "## Strategy Parameters" in result

    def test_whitelist_as_json_array(self, strategy_config):
        """Test that whitelist parsing works with JSON string."""
        builder = PromptBuilder(strategy_config)

        market_context = MarketContext(
            tickers=[{"symbol": "BTCUSDT", "price": "65000.00", "volume_24h": "1000.00", "bid": "64999.00", "ask": "65001.00"}],
        )
        portfolio_context = PortfolioContext(
            balances=[],
            total_usdt_value="0",
            open_positions={},
        )

        result = builder.build(market_context, portfolio_context, strategy_config)

        # Should still show the whitelist items
        assert "BTCUSDT" in result

    def test_constraints_shows_whitelist(self, builder, market_context, portfolio_context, strategy_config):
        """Test that constraints section includes the whitelist."""
        result = builder.build(market_context, portfolio_context, strategy_config)

        assert "Constraints Reminder" in result
        assert "Only trade symbols in the whitelist" in result

    def test_market_snapshot_table_format(self, builder, market_context, portfolio_context, strategy_config):
        """Test that market snapshot is formatted as tables."""
        result = builder.build(market_context, portfolio_context, strategy_config)

        # Should contain table headers
        assert "| Symbol | Price | 24h Volume" in result or "Symbol" in result

    def test_no_timestamps_for_determinism(self, builder, market_context, portfolio_context, strategy_config):
        """Test that prompt doesn't include timestamps that would break determinism."""
        result = builder.build(market_context, portfolio_context, strategy_config)

        # Should not contain common timestamp patterns
        import re
        timestamp_patterns = [
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}',  # ISO timestamp
            r'\d{2}:\d{2}:\d{2}',  # Time only
        ]

        for pattern in timestamp_patterns:
            matches = re.findall(pattern, result)
            # Should not have multiple timestamp-like strings
            assert len(matches) == 0, f"Found timestamp-like strings: {matches}"


class TestPromptBuilderSections:
    """Tests for individual sections of the prompt."""

    @pytest.fixture
    def strategy_config(self):
        return {
            "asset_whitelist": '["BTCUSDT", "ETHUSDT"]',
            "max_position_pct": 20.0,
            "max_daily_loss_pct": 5.0,
            "min_confidence_threshold": 75,
            "max_risk_tier": "low",
        }

    @pytest.fixture
    def builder(self, strategy_config):
        return PromptBuilder(strategy_config)

    def test_system_persona_exact_text(self, builder):
        """Test that system persona contains exact required text."""
        persona = builder._build_system_persona()

        assert "You are Horizon" in persona
        assert "quantitative trading analyst specializing in long-term crypto asset positioning" in persona
        assert "analysis horizon is 8 hours" in persona
        assert "disciplined, data-driven trade recommendations" in persona

    def test_instructions_complete(self, builder):
        """Test that all instruction points are included."""
        instructions = builder._build_instructions()

        assert "Analyze the provided data" in instructions
        assert "zero trades" in instructions
        assert "exchange, symbol, side, order_type, price" in instructions
        assert "confidence_score (0-100)" in instructions
        assert "risk_tier (low/medium/high)" in instructions
        assert "portfolio concentration" in instructions
        assert "technical indicator confluence" in instructions
        assert "higher confidence" in instructions

    def test_response_format_mandatory(self, builder):
        """Test that response format section is marked as mandatory."""
        response_format = builder._build_response_format()

        assert "MANDATORY" in response_format
        assert "valid JSON" in response_format
        assert "NOTHING ELSE" in response_format
        assert "No markdown" in response_format
        assert "no explanations outside the JSON" in response_format

    def test_constraints_reminder_exact_requirements(self, builder, strategy_config):
        """Test that constraints reminder contains exact requirements."""
        constraints = builder._build_constraints(strategy_config)

        assert "Only trade symbols in the whitelist" in constraints
        assert "Confidence score must be honest" in constraints
        assert "do not inflate scores" in constraints
        assert "Risk tier must reflect actual risk" in constraints
        assert "not desired risk" in constraints


class TestMarketContextFormatting:
    """Tests for MarketContext formatting in prompts."""

    @pytest.fixture
    def strategy_config(self):
        return {
            "asset_whitelist": '["BTCUSDT"]',
            "max_position_pct": 20.0,
            "max_daily_loss_pct": 5.0,
            "min_confidence_threshold": 75,
            "max_risk_tier": "low",
        }

    @pytest.fixture
    def builder(self, strategy_config):
        return PromptBuilder(strategy_config)

    def test_ticker_bid_ask_spread_calculation(self, builder, strategy_config):
        """Test that bid-ask spread is calculated correctly."""
        market_context = MarketContext(
            tickers=[
                {"symbol": "BTCUSDT", "price": "65000.00", "volume_24h": "1000.00", "bid": "64999.00", "ask": "65001.00"},
            ],
        )
        portfolio_context = PortfolioContext(balances=[], total_usdt_value="0", open_positions={})

        result = builder.build(market_context, portfolio_context, strategy_config)

        # Spread should be 2.00 (65001 - 64999)
        assert "2.00" in result

    def test_technical_indicators_all_fields(self, builder, strategy_config):
        """Test that all technical indicator fields are included."""
        market_context = MarketContext(
            tickers=[],
            technical_indicators={
                "BTCUSDT": {
                    "rsi_14": 65.5,
                    "macd_line": 150.25,
                    "macd_signal": 120.30,
                    "macd_histogram": 29.95,
                    "ema_20": 64000.00,
                    "ema_50": 63000.00,
                    "bollinger_upper": 67000.00,
                    "bollinger_lower": 62000.00,
                    "atr_14": 850.00,
                },
            },
        )
        portfolio_context = PortfolioContext(balances=[], total_usdt_value="0", open_positions={})

        result = builder.build(market_context, portfolio_context, strategy_config)

        assert "RSI" in result or "rsi" in result.lower()
        assert "MACD" in result
        assert "EMA" in result
        assert "Bollinger" in result
        assert "ATR" in result

    def test_format_value_large_numbers(self, builder):
        """Test that large numbers are formatted with commas."""
        assert builder._format_value(1234567.89) == "1,234,567.89"
        assert builder._format_value(99999999.00) == "99,999,999.00"

    def test_format_value_small_numbers(self, builder):
        """Test that small numbers show enough precision."""
        result = builder._format_value(0.00012345)
        assert "0001" in result or "0.0001" in result

    def test_format_value_none(self, builder):
        """Test that None values return N/A."""
        assert builder._format_value(None) == "N/A"

    def test_format_value_invalid(self, builder):
        """Test that invalid values return N/A."""
        assert builder._format_value("invalid") == "N/A"
        assert builder._format_value("not a number") == "N/A"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])