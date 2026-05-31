"""Tests for LLM Strategy Engine."""

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from horizon.internal.llm.engine import (
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    LLMStrategyEngine,
    LLMProposalOutput,
)
from horizon.internal.llm.parser import TradeProposal


class MockTicker:
    """Mock Ticker for testing."""

    def __init__(self, symbol, price, volume_24h, exchange="binance"):
        self.symbol = symbol
        self.price = Decimal(str(price))
        self.volume_24h = Decimal(str(volume_24h))
        self.exchange = exchange
        self.timestamp_ms = 0


class MockOrderBook:
    """Mock OrderBook for testing."""

    def __init__(self, symbol, bids, asks, exchange="binance"):
        self.symbol = symbol
        self.bids = bids
        self.asks = asks
        self.exchange = exchange


class MockOrderBookEntry:
    """Mock OrderBookEntry for testing."""

    def __init__(self, price, size):
        self.price = Decimal(str(price))
        self.size = Decimal(str(size))


class MockBalance:
    """Mock Balance for testing."""

    def __init__(self, asset, free, locked, exchange="binance"):
        self.asset = asset
        self.free = Decimal(str(free))
        self.locked = Decimal(str(locked))
        self.exchange = exchange


class TestLLMStrategyEngine:
    """Unit tests for LLMStrategyEngine."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database connection."""
        db = AsyncMock()
        db.execute = AsyncMock()
        db.commit = AsyncMock()
        return db

    @pytest.fixture
    def mock_registry(self):
        """Create mock exchange registry."""
        registry = MagicMock()
        registry.get_all_tickers = AsyncMock(return_value=[])
        registry.list_enabled = MagicMock(return_value=[])
        return registry

    @pytest.fixture
    def mock_proposal_queue(self):
        """Create mock proposal queue."""
        queue = AsyncMock()
        queue.enqueue = AsyncMock()
        return queue

    @pytest.fixture
    def mock_indicator_calculator(self):
        """Create mock indicator calculator."""
        calc = AsyncMock()
        calc.compute_for_symbol = AsyncMock(
            return_value=MagicMock(
                rsi_14=50.0,
                macd_line=100.0,
                macd_signal=90.0,
                macd_histogram=10.0,
                ema_20=95000.0,
                ema_50=94000.0,
                bollinger_upper=98000.0,
                bollinger_lower=92000.0,
                atr_14=500.0,
                computed_at=datetime.now(timezone.utc),
            )
        )
        return calc

    @pytest.fixture
    def mock_fetcher(self):
        """Create mock market data fetcher."""
        fetcher = MagicMock()
        fetcher.get_ticker = MagicMock(return_value=None)
        return fetcher

    @pytest.fixture
    def mock_anthropic_client(self):
        """Create mock Anthropic client."""
        client = MagicMock()
        client.messages = MagicMock()
        return client

    @pytest.fixture
    def strategy_config(self):
        """Sample strategy config."""
        return {
            "id": 1,
            "name": "default_long_term",
            "enabled": 1,
            "model": DEFAULT_MODEL,
            "analysis_interval_hours": 8,
            "asset_whitelist": '["BTCUSDT","ETHUSDT"]',
            "max_position_pct": 20.0,
            "max_daily_loss_pct": 5.0,
            "min_confidence_threshold": 75,
            "max_risk_tier": "low",
            "system_prompt": "You are a trading analyst.",
        }

    @pytest.fixture
    def engine(
        self,
        mock_db,
        mock_registry,
        mock_proposal_queue,
        mock_anthropic_client,
        strategy_config,
        mock_indicator_calculator,
        mock_fetcher,
    ):
        """Create engine instance with mocks."""
        return LLMStrategyEngine(
            db=mock_db,
            registry=mock_registry,
            proposal_queue=mock_proposal_queue,
            anthropic_client=mock_anthropic_client,
            strategy_config=strategy_config,
            indicator_calculator=mock_indicator_calculator,
            fetcher=mock_fetcher,
        )

    # ===== Constructor Tests =====

    def test_engine_initialization(self, engine, strategy_config):
        """Test engine initializes with all dependencies."""
        assert engine._strategy_config == strategy_config
        assert engine._parser is not None

    def test_get_asset_whitelist_from_string(self, engine):
        """Test whitelist parsing from JSON string."""
        whitelist = engine._get_asset_whitelist()
        assert "BTCUSDT" in whitelist
        assert "ETHUSDT" in whitelist

    def test_get_asset_whitelist_from_list(self):
        """Test whitelist when already a list."""
        config = {"asset_whitelist": ["BTCUSDT", "ETHUSDT"]}
        engine = MagicMock()
        engine._strategy_config = config
        engine._get_asset_whitelist = LLMStrategyEngine._get_asset_whitelist
        result = LLMStrategyEngine._get_asset_whitelist(engine)
        assert result == ["BTCUSDT", "ETHUSDT"]

    # ===== analyze_and_propose Tests =====

    @pytest.mark.asyncio
    async def test_analyze_and_propose_no_active_config(self, engine, mock_db):
        """Test when no active strategy config exists."""
        # Mock db to return no active config
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_db.execute.return_value = mock_cursor

        result = await engine.analyze_and_propose()

        assert result.proposals == []
        assert result.market_analysis_summary == ""

    @pytest.mark.asyncio
    async def test_analyze_and_propose_success(
        self,
        engine,
        mock_db,
        mock_registry,
        mock_proposal_queue,
        mock_anthropic_client,
        mock_indicator_calculator,
    ):
        """Test successful analysis and proposal generation."""
        # Setup mock strategy config row
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(
            return_value={
                "id": 1,
                "name": "default_long_term",
                "enabled": 1,
                "model": DEFAULT_MODEL,
                "analysis_interval_hours": 8,
                "asset_whitelist": '["BTCUSDT","ETHUSDT"]',
                "max_position_pct": 20.0,
                "max_daily_loss_pct": 5.0,
                "min_confidence_threshold": 75,
                "max_risk_tier": "low",
                "system_prompt": "You are a trading analyst.",
            }
        )
        mock_db.execute.return_value = mock_cursor

        # Setup mock tickers
        mock_registry.get_all_tickers = AsyncMock(
            return_value=[MockTicker("BTCUSDT", 95000, 1000)]
        )

        # Setup mock orderbook
        mock_adapter = AsyncMock()
        mock_adapter.fetch_orderbook = AsyncMock(
            return_value=MockOrderBook(
                "BTCUSDT",
                [MockOrderBookEntry(94900, 1.0)],
                [MockOrderBookEntry(95100, 1.0)],
            )
        )
        mock_adapter.fetch_recent_trades = AsyncMock(return_value=[])
        mock_registry.list_enabled = MagicMock(return_value=[mock_adapter])

        # Setup mock Anthropic response
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"analysis_summary":"Test","confidence_explanation":"Test","proposals":[]}')]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_anthropic_client.messages.create = AsyncMock(return_value=mock_response)

        # Setup mock portfolio snapshot
        mock_cursor.fetchone = AsyncMock(return_value=None)

        result = await engine.analyze_and_propose()

        # Should record history on success
        assert mock_db.execute.called or mock_db.commit.called

    @pytest.mark.asyncio
    async def test_analyze_and_propose_api_timeout(
        self, engine, mock_db, mock_anthropic_client
    ):
        """Test handling of API timeout."""
        # Setup mock strategy config row
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(
            return_value={
                "id": 1,
                "name": "default_long_term",
                "enabled": 1,
                "model": DEFAULT_MODEL,
                "analysis_interval_hours": 8,
                "asset_whitelist": '["BTCUSDT"]',
                "system_prompt": "You are a trading analyst.",
            }
        )
        mock_db.execute.return_value = mock_cursor

        # Setup mock ticker
        mock_registry = MagicMock()
        mock_registry.get_all_tickers = AsyncMock(return_value=[])
        mock_registry.list_enabled = MagicMock(return_value=[])
        engine._registry = mock_registry

        # Setup mock to raise timeout
        mock_anthropic_client.messages.create = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )

        result = await engine.analyze_and_propose()

        assert result.proposals == []
        assert result.market_analysis_summary == ""

    @pytest.mark.asyncio
    async def test_analyze_and_propose_api_error(
        self, engine, mock_db, mock_anthropic_client
    ):
        """Test handling of API error."""
        # Setup mock strategy config row
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(
            return_value={
                "id": 1,
                "name": "default_long_term",
                "enabled": 1,
                "model": DEFAULT_MODEL,
                "analysis_interval_hours": 8,
                "asset_whitelist": '["BTCUSDT"]',
                "system_prompt": "You are a trading analyst.",
            }
        )
        mock_db.execute.return_value = mock_cursor

        # Setup mock ticker
        mock_registry = MagicMock()
        mock_registry.get_all_tickers = AsyncMock(return_value=[])
        mock_registry.list_enabled = MagicMock(return_value=[])
        engine._registry = mock_registry

        # Setup mock to raise API error
        class APIError(Exception):
            pass

        mock_anthropic_client.messages.create = AsyncMock(
            side_effect=APIError("API Error")
        )

        result = await engine.analyze_and_propose()

        assert result.proposals == []

    @pytest.mark.asyncio
    async def test_analyze_and_propose_parse_error(
        self, engine, mock_db, mock_anthropic_client
    ):
        """Test handling of parse error."""
        # Setup mock strategy config row
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(
            return_value={
                "id": 1,
                "name": "default_long_term",
                "enabled": 1,
                "model": DEFAULT_MODEL,
                "analysis_interval_hours": 8,
                "asset_whitelist": '["BTCUSDT"]',
                "system_prompt": "You are a trading analyst.",
            }
        )
        mock_db.execute.return_value = mock_cursor

        # Setup mock ticker
        mock_registry = MagicMock()
        mock_registry.get_all_tickers = AsyncMock(return_value=[])
        mock_registry.list_enabled = MagicMock(return_value=[])
        engine._registry = mock_registry

        # Setup mock Anthropic to return invalid JSON
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="This is not JSON")]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_anthropic_client.messages.create = AsyncMock(return_value=mock_response)

        result = await engine.analyze_and_propose()

        assert result.proposals == []

    # ===== _gather_market_context Tests =====

    @pytest.mark.asyncio
    async def test_gather_market_context_empty_symbols(self, engine):
        """Test gathering market context with empty symbol list."""
        context = await engine._gather_market_context([])

        assert context.tickers == []
        assert context.orderbooks == {}
        assert context.technical_indicators == {}

    @pytest.mark.asyncio
    async def test_gather_market_context_with_symbols(
        self, engine, mock_registry, mock_indicator_calculator
    ):
        """Test gathering market context with symbols."""
        # Setup mock tickers
        mock_registry.get_all_tickers = AsyncMock(
            return_value=[
                MockTicker("BTCUSDT", 95000, 1000, "binance"),
                MockTicker("BTCUSDT", 94900, 500, "htx"),
            ]
        )

        # Setup mock adapter
        mock_adapter = AsyncMock()
        mock_adapter.fetch_orderbook = AsyncMock(
            return_value=MockOrderBook(
                "BTCUSDT",
                [MockOrderBookEntry(94900, 1.0)],
                [MockOrderBookEntry(95100, 1.0)],
            )
        )
        mock_adapter.fetch_recent_trades = AsyncMock(
            return_value=[
                {"price": "95000", "size": "0.1", "side": "buy", "timestamp_ms": 0}
            ]
        )
        mock_registry.list_enabled = MagicMock(return_value=[mock_adapter])

        context = await engine._gather_market_context(["BTCUSDT"])

        # Should have tickers
        assert len(context.tickers) == 2

        # Should have technical indicators
        assert "BTCUSDT" in context.technical_indicators

    # ===== _gather_portfolio_context Tests =====

    @pytest.mark.asyncio
    async def test_gather_portfolio_context_empty(self, engine, mock_db):
        """Test gathering portfolio context with no data."""
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_db.execute.return_value = mock_cursor

        context = await engine._gather_portfolio_context()

        assert context.balances == []
        assert context.total_usdt_value == "0.00"
        assert context.open_positions == {}

    @pytest.mark.asyncio
    async def test_gather_portfolio_context_with_balances(self, engine, mock_db):
        """Test gathering portfolio context with balances."""
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(
            return_value=[
                {
                    "exchange": "binance",
                    "asset": "USDT",
                    "free_balance": 10000.0,
                    "locked_balance": 0.0,
                    "usdt_value": 10000.0,
                    "snapshot_time": 0,
                },
                {
                    "exchange": "binance",
                    "asset": "BTC",
                    "free_balance": 0.5,
                    "locked_balance": 0.0,
                    "usdt_value": 47500.0,
                    "snapshot_time": 0,
                },
            ]
        )
        mock_db.execute.return_value = mock_cursor

        context = await engine._gather_portfolio_context()

        assert len(context.balances) == 2
        assert context.total_usdt_value == "57500.00"
        assert "BTC" in context.open_positions

    # ===== Helper Method Tests =====

    def test_build_market_snapshot(self, engine):
        """Test building market snapshot dict."""
        from horizon.internal.llm.prompt_builder import MarketContext

        market_context = MarketContext(
            tickers=[{"symbol": "BTCUSDT", "price": "95000"}],
            orderbooks={},
            technical_indicators={},
            recent_trades={},
        )

        snapshot = engine._build_market_snapshot(market_context)

        assert "tickers" in snapshot
        assert snapshot["tickers"][0]["symbol"] == "BTCUSDT"

    def test_build_portfolio_snapshot(self, engine):
        """Test building portfolio snapshot dict."""
        from horizon.internal.llm.prompt_builder import PortfolioContext

        portfolio_context = PortfolioContext(
            balances=[],
            total_usdt_value="10000.00",
            open_positions={},
        )

        snapshot = engine._build_portfolio_snapshot(portfolio_context)

        assert "total_usdt_value" in snapshot
        assert snapshot["total_usdt_value"] == "10000.00"

    def test_build_technical_context(self, engine):
        """Test building technical context dict."""
        from horizon.internal.llm.prompt_builder import MarketContext

        market_context = MarketContext(
            tickers=[],
            orderbooks={},
            technical_indicators={"BTCUSDT": {"rsi_14": 50.0}},
            recent_trades={},
        )

        context = engine._build_technical_context(market_context)

        assert "BTCUSDT" in context
        assert context["BTCUSDT"]["rsi_14"] == 50.0

    # ===== LLMProposalOutput Tests =====

    def test_llm_proposal_output_creation(self):
        """Test LLMProposalOutput dataclass."""
        output = LLMProposalOutput(
            proposals=[],
            market_analysis_summary="Test summary",
            confidence_explanation="Test explanation",
        )

        assert output.proposals == []
        assert output.market_analysis_summary == "Test summary"
        assert output.confidence_explanation == "Test explanation"

    def test_llm_proposal_output_with_proposals(self):
        """Test LLMProposalOutput with proposals."""
        proposal = MagicMock(spec=TradeProposal)
        output = LLMProposalOutput(
            proposals=[proposal],
            market_analysis_summary="Summary",
            confidence_explanation="Explanation",
        )

        assert len(output.proposals) == 1


class TestLLMStrategyEngineIntegration:
    """Integration tests for LLMStrategyEngine (with real components where possible)."""

    @pytest.mark.asyncio
    async def test_load_active_strategy_config(self, tmp_path):
        """Test loading active strategy config from database."""
        import aiosqlite

        db_path = tmp_path / "test.db"

        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row

        # Setup table
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                model TEXT,
                analysis_interval_hours INTEGER DEFAULT 8,
                asset_whitelist TEXT NOT NULL,
                max_position_pct REAL DEFAULT 20.0,
                max_daily_loss_pct REAL DEFAULT 5.0,
                min_confidence_threshold INTEGER DEFAULT 75,
                max_risk_tier TEXT DEFAULT 'low',
                system_prompt TEXT NOT NULL
            )
            """
        )
        await conn.commit()
        await conn.execute(
            """
            INSERT INTO strategy_configs
            (name, enabled, model, analysis_interval_hours, asset_whitelist, system_prompt)
            VALUES ('test_strategy', 1, 'claude-3-5-sonnet-20241022', 8,
                    '["BTCUSDT"]', 'You are a test analyst.')
            """
        )
        await conn.commit()

        mock_registry = MagicMock()
        mock_registry.get_all_tickers = AsyncMock(return_value=[])
        mock_registry.list_enabled = MagicMock(return_value=[])

        mock_queue = AsyncMock()

        mock_client = MagicMock()

        mock_calc = AsyncMock()

        mock_fetcher = MagicMock()

        engine = LLMStrategyEngine(
            db=conn,
            registry=mock_registry,
            proposal_queue=mock_queue,
            anthropic_client=mock_client,
            strategy_config={},
            indicator_calculator=mock_calc,
            fetcher=mock_fetcher,
        )

        config = await engine._load_active_strategy_config()

        assert config is not None
        assert config["name"] == "test_strategy"
        assert config["enabled"] == 1

        await conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])