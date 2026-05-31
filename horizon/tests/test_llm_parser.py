"""Tests for LLM Response Parser."""

import json
from datetime import datetime, timezone

import pytest

from horizon.internal.llm.parser import (
    LLMParseError,
    LLMResponseParser,
    TradeProposal,
    VALID_EXCHANGES,
    VALID_ORDER_TYPES,
    VALID_RISK_TIERS,
    VALID_SIDES,
)


class TestLLMResponseParser:
    """Unit tests for LLMResponseParser."""

    @pytest.fixture
    def parser(self):
        """Create parser instance with whitelist."""
        return LLMResponseParser(
            asset_whitelist=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
        )

    @pytest.fixture
    def market_snapshot(self):
        """Sample market snapshot."""
        return {
            "BTCUSDT": {
                "last_price": "95000.00",
                "bid_price": "94900.00",
                "ask_price": "95100.00",
                "volume_24h": "12345.67",
            },
            "ETHUSDT": {
                "last_price": "3500.00",
                "bid_price": "3490.00",
                "ask_price": "3510.00",
                "volume_24h": "67890.12",
            },
        }

    @pytest.fixture
    def portfolio_snapshot(self):
        """Sample portfolio snapshot."""
        return {
            "total_value_usdt": 100000.0,
            "positions": {
                "BTCUSDT": {"quantity": 0.5, "value_usdt": 47500.0},
                "ETHUSDT": {"quantity": 5.0, "value_usdt": 17500.0},
            },
        }

    @pytest.fixture
    def technical_context(self):
        """Sample technical context."""
        return {
            "BTCUSDT": {
                "rsi_14": 65.5,
                "macd_line": 150.0,
                "ema_20": 94000.0,
            },
            "ETHUSDT": {
                "rsi_14": 58.2,
                "macd_line": 25.0,
                "ema_20": 3450.0,
            },
        }

    # ===== JSON Extraction Tests =====

    def test_extract_json_from_markdown_code_block(self, parser):
        """Test JSON extraction from markdown code block."""
        text = '''
        ```json
        {"analysis_summary": "Test", "confidence_explanation": "Test", "proposals": []}
        ```
        '''
        result = parser._extract_json(text)
        assert result["analysis_summary"] == "Test"
        assert result["proposals"] == []

    def test_extract_json_from_plain_text(self, parser):
        """Test JSON extraction from plain text."""
        text = '{"analysis_summary": "Test", "confidence_explanation": "Test", "proposals": []}'
        result = parser._extract_json(text)
        assert result["analysis_summary"] == "Test"

    def test_extract_json_no_valid_json_raises_error(self, parser):
        """Test that non-JSON text raises LLMParseError."""
        text = "This is not JSON at all"
        with pytest.raises(LLMParseError, match="No valid JSON found"):
            parser._extract_json(text)

    def test_extract_json_invalid_json_raises_error(self, parser):
        """Test that invalid JSON raises LLMParseError."""
        text = '{"analysis_summary": "Test", missing quotes}'
        with pytest.raises(LLMParseError):
            parser._extract_json(text)

    # ===== Schema Validation Tests =====

    def test_parse_missing_analysis_summary_raises_error(self, parser):
        """Test missing analysis_summary key raises error."""
        raw = '{"confidence_explanation": "Test", "proposals": []}'
        with pytest.raises(LLMParseError, match="Missing key: analysis_summary"):
            parser.parse(raw, {}, {}, {})

    def test_parse_missing_confidence_explanation_raises_error(self, parser):
        """Test missing confidence_explanation key raises error."""
        raw = '{"analysis_summary": "Test", "proposals": []}'
        with pytest.raises(LLMParseError, match="Missing key: confidence_explanation"):
            parser.parse(raw, {}, {}, {})

    def test_parse_missing_proposals_raises_error(self, parser):
        """Test missing proposals key raises error."""
        raw = '{"analysis_summary": "Test", "confidence_explanation": "Test"}'
        with pytest.raises(LLMParseError, match="Missing key: proposals"):
            parser.parse(raw, {}, {}, {})

    def test_parse_proposals_not_list_raises_error(self, parser):
        """Test proposals must be a list."""
        raw = '{"analysis_summary": "Test", "confidence_explanation": "Test", "proposals": "not a list"}'
        with pytest.raises(LLMParseError, match="proposals must be a list"):
            parser.parse(raw, {}, {}, {})

    # ===== Valid JSON Parsing Tests =====

    def test_parse_valid_json_single_proposal(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test valid JSON response parses into TradeProposal list."""
        raw = json.dumps({
            "analysis_summary": "Bullish on BTC",
            "confidence_explanation": "High confidence based on RSI",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                }
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert len(proposals) == 1
        proposal = proposals[0]
        assert isinstance(proposal, TradeProposal)
        assert proposal.exchange == "binance"
        assert proposal.symbol == "BTCUSDT"
        assert proposal.side == "buy"
        assert proposal.order_type == "market"
        assert proposal.volume == 0.1
        assert proposal.confidence_score == 85
        assert proposal.risk_tier == "low"
        assert proposal.status == "proposed"
        assert proposal.proposed_price == 95000.0
        assert proposal.llm_rationale == "RSI oversold"

    def test_parse_valid_json_multiple_proposals(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test valid JSON with multiple proposals."""
        raw = json.dumps({
            "analysis_summary": "Multiple opportunities",
            "confidence_explanation": "Various signals",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                },
                {
                    "exchange": "htx",
                    "symbol": "ETHUSDT",
                    "side": "sell",
                    "order_type": "limit",
                    "price": 3600.00,
                    "volume": 2.0,
                    "confidence_score": 72,
                    "risk_tier": "medium",
                    "rationale": "Overbought",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert len(proposals) == 2
        assert proposals[0].symbol == "BTCUSDT"
        assert proposals[0].exchange == "binance"
        assert proposals[1].symbol == "ETHUSDT"
        assert proposals[1].exchange == "htx"
        assert proposals[1].price == 3600.0

    def test_parse_empty_proposals_array_returns_empty_list(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test empty proposals array returns empty list without error."""
        raw = json.dumps({
            "analysis_summary": "No opportunities",
            "confidence_explanation": "No signals meet criteria",
            "proposals": [],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert proposals == []

    # ===== Missing Fields Tests =====

    def test_parse_missing_required_field_skips_proposal(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test missing required field skips proposal without failing entire parse."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    # Missing symbol
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                },
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        # Should return1 valid proposal, skipping the invalid one
        assert len(proposals) == 1
        assert proposals[0].symbol == "BTCUSDT"

    def test_parse_missing_multiple_fields_skips_proposal(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test proposal with multiple missing fields is skipped."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    # Missing symbol, side, etc.
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert proposals == []

    # ===== Invalid Exchange Tests =====

    def test_parse_invalid_exchange_skips_proposal(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test invalid exchange value skips proposal."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "invalid_exchange",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert proposals == []

    # ===== Invalid Side Tests =====

    def test_parse_invalid_side_skips_proposal(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test invalid side value skips proposal."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "hold",  # Invalid
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert proposals == []

    # ===== Invalid Order Type Tests =====

    def test_parse_invalid_order_type_skips_proposal(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test invalid order_type value skips proposal."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "stop_limit",  # Invalid
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert proposals == []

    # ===== Invalid Symbol Tests =====

    def test_parse_symbol_not_in_whitelist_skips_proposal(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test symbol not in whitelist skips proposal."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "DOGEUSDT",  # Not in whitelist
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert proposals == []

    # ===== Confidence Score Tests =====

    def test_parse_confidence_score_below_0_clamped(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test confidence_score below 0 is clamped to 0."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": -10,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert len(proposals) == 1
        assert proposals[0].confidence_score == 0

    def test_parse_confidence_score_above_100_clamped(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test confidence_score above 100 is clamped to 100."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 150,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert len(proposals) == 1
        assert proposals[0].confidence_score == 100

    def test_parse_confidence_score_float_converted(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test non-integer confidence_score is converted to int."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 85.7,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert len(proposals) == 1
        assert proposals[0].confidence_score == 85

    # ===== Risk Tier Tests =====

    def test_parse_invalid_risk_tier_defaults_to_medium(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test invalid risk_tier defaults to medium."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "extreme",  # Invalid
                    "rationale": "RSI oversold",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert len(proposals) == 1
        assert proposals[0].risk_tier == "medium"

    # ===== Limit Order Price Tests =====

    def test_parse_limit_order_without_price_skips_proposal(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test limit order without price is skipped."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "limit",
                    # Missing price
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert proposals == []

    def test_parse_limit_order_with_valid_price(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test limit order with price is accepted."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "limit",
                    "price": 94000.00,
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "Buy the dip",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert len(proposals) == 1
        assert proposals[0].order_type == "limit"
        assert proposals[0].price == 94000.0

    # ===== Volume Parsing Tests =====

    def test_parse_volume_as_string(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test volume parsed from string."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "market",
                    "volume": "0.5",
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert len(proposals) == 1
        assert proposals[0].volume == 0.5

    def test_parse_invalid_volume_skips_proposal(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test invalid volume skips proposal."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "market",
                    "volume": "invalid",
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert proposals == []

    # ===== TradeProposal Properties Tests =====

    def test_proposal_is_expired(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test is_expired property."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert len(proposals) == 1
        assert proposals[0].is_expired is False

    def test_proposal_is_long(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test is_long property."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert proposals[0].is_long is True
        assert proposals[0].is_short is False

    def test_proposal_is_short(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test is_short property."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "sell",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "RSI overbought",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert proposals[0].is_short is True
        assert proposals[0].is_long is False

    # ===== JSON Serialization Tests =====

    def test_proposal_to_dict(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test to_dict serialization."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "RSI oversold",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert len(proposals) == 1
        d = proposals[0].to_dict()
        assert d["exchange"] == "binance"
        assert d["symbol"] == "BTCUSDT"
        assert d["side"] == "buy"
        assert d["status"] == "proposed"
        assert "market_snapshot" in d
        assert "portfolio_snapshot" in d

    # ===== All Exchanges Valid Tests =====

    @pytest.mark.parametrize("exchange", VALID_EXCHANGES)
    def test_parse_all_valid_exchanges(self, exchange, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test all valid exchanges are accepted."""
        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": exchange,
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "Test",
                },
            ],
        })

        proposals = parser.parse(raw, market_snapshot, portfolio_snapshot, technical_context)

        assert len(proposals) == 1
        assert proposals[0].exchange == exchange

    # ===== Market Price Lookup Tests =====

    def test_parse_market_snapshot_format_exchange_symbol(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test market snapshot with exchange->symbol format."""
        # Format: {exchange: {symbol: {price: value}}}
        snapshot = {
            "binance": {
                "BTCUSDT": {
                    "last_price": "95000.00",
                },
            },
        }

        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "Test",
                },
            ],
        })

        proposals = parser.parse(raw, snapshot, portfolio_snapshot, technical_context)

        assert len(proposals) == 1
        assert proposals[0].proposed_price == 95000.0

    def test_parse_market_snapshot_format_tickers(self, parser, market_snapshot, portfolio_snapshot, technical_context):
        """Test market snapshot with tickers format."""
        # Format: {tickers: {symbol: {price: value}}}
        snapshot = {
            "tickers": {
                "BTCUSDT": {
                    "last_price": "96000.00",
                },
            },
        }

        raw = json.dumps({
            "analysis_summary": "Test",
            "confidence_explanation": "Test",
            "proposals": [
                {
                    "exchange": "binance",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "order_type": "market",
                    "volume": 0.1,
                    "confidence_score": 85,
                    "risk_tier": "low",
                    "rationale": "Test",
                },
            ],
        })

        proposals = parser.parse(raw, snapshot, portfolio_snapshot, technical_context)

        assert len(proposals) == 1
        assert proposals[0].proposed_price == 96000.0


class TestLLMParseError:
    """Tests for LLMParseError exception."""

    def test_llm_parse_error_is_exception(self):
        """Test LLMParseError inherits from Exception."""
        error = LLMParseError("test message")
        assert isinstance(error, Exception)

    def test_llm_parse_error_message(self):
        """Test LLMParseError message."""
        error = LLMParseError("test message")
        assert str(error) == "test message"


class TestTradeProposal:
    """Tests for TradeProposal dataclass."""

    def test_trade_proposal_creation(self):
        """Test TradeProposal can be created."""
        proposal = TradeProposal(
            id="test-id",
            status="proposed",
            exchange="binance",
            symbol="BTCUSDT",
            side="buy",
            order_type="market",
            price=None,
            volume=0.1,
            confidence_score=85,
            risk_tier="low",
            llm_rationale="Test rationale",
            llm_raw_response="raw",
            technical_context="{}",
            market_snapshot="{}",
            portfolio_snapshot=None,
            guardrail_result=None,
            approved_by=None,
            approved_at=None,
            executed_order_id=None,
            expires_at=datetime.now(timezone.utc),
            price_drift_threshold_pct=3.0,
            proposed_price=95000.0,
            created_at=datetime.now(timezone.utc),
        )

        assert proposal.id == "test-id"
        assert proposal.status == "proposed"
        assert proposal.confidence_score == 85

    def test_trade_proposal_is_long(self):
        """Test is_long property."""
        proposal = TradeProposal(
            id="test-id",
            status="proposed",
            exchange="binance",
            symbol="BTCUSDT",
            side="buy",
            order_type="market",
            price=None,
            volume=0.1,
            confidence_score=85,
            risk_tier="low",
            llm_rationale="Test",
            llm_raw_response="raw",
            technical_context="{}",
            market_snapshot="{}",
            portfolio_snapshot=None,
            guardrail_result=None,
            approved_by=None,
            approved_at=None,
            executed_order_id=None,
            expires_at=datetime.now(timezone.utc),
            price_drift_threshold_pct=3.0,
            proposed_price=95000.0,
            created_at=datetime.now(timezone.utc),
        )

        assert proposal.is_long is True
        assert proposal.is_short is False

    def test_trade_proposal_is_short(self):
        """Test is_short property."""
        proposal = TradeProposal(
            id="test-id",
            status="proposed",
            exchange="binance",
            symbol="BTCUSDT",
            side="sell",
            order_type="market",
            price=None,
            volume=0.1,
            confidence_score=85,
            risk_tier="low",
            llm_rationale="Test",
            llm_raw_response="raw",
            technical_context="{}",
            market_snapshot="{}",
            portfolio_snapshot=None,
            guardrail_result=None,
            approved_by=None,
            approved_at=None,
            executed_order_id=None,
            expires_at=datetime.now(timezone.utc),
            price_drift_threshold_pct=3.0,
            proposed_price=95000.0,
            created_at=datetime.now(timezone.utc),
        )

        assert proposal.is_short is True
        assert proposal.is_long is False

    def test_trade_proposal_to_dict(self):
        """Test to_dict serialization."""
        now = datetime.now(timezone.utc)
        proposal = TradeProposal(
            id="test-id",
            status="proposed",
            exchange="binance",
            symbol="BTCUSDT",
            side="buy",
            order_type="market",
            price=None,
            volume=0.1,
            confidence_score=85,
            risk_tier="low",
            llm_rationale="Test",
            llm_raw_response="raw",
            technical_context="{}",
            market_snapshot="{}",
            portfolio_snapshot=None,
            guardrail_result=None,
            approved_by=None,
            approved_at=None,
            executed_order_id=None,
            expires_at=now,
            price_drift_threshold_pct=3.0,
            proposed_price=95000.0,
            created_at=now,
        )

        d = proposal.to_dict()
        assert d["id"] == "test-id"
        assert d["symbol"] == "BTCUSDT"
        assert d["confidence_score"] == 85
        assert "expires_at" in d
        assert "created_at" in d


if __name__ == "__main__":
    pytest.main([__file__, "-v"])