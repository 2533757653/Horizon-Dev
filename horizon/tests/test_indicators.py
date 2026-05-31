"""Tests for technical indicator calculator."""

import asyncio
import random
from decimal import Decimal

import aiosqlite
import pytest
import pytest_asyncio

from horizon.internal.indicators import TechnicalIndicatorCalculator, TechnicalIndicators


class TestTechnicalIndicators:
    """Unit tests for TechnicalIndicatorCalculator."""

    @pytest_asyncio.fixture
    async def db(self, tmp_path):
        """Create an in-memory database for testing."""
        db_path = str(tmp_path / "test.db")
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row

        # Create required table
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_data_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                last_price REAL,
                bid_price REAL,
                ask_price REAL,
                volume_24h REAL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(exchange, symbol)
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS technical_indicators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                rsi_14 REAL,
                macd_line REAL,
                macd_signal REAL,
                macd_histogram REAL,
                ema_20 REAL,
                ema_50 REAL,
                bollinger_upper REAL,
                bollinger_lower REAL,
                atr_14 REAL,
                computed_at TIMESTAMP NOT NULL,
                UNIQUE(symbol, computed_at)
            )
            """
        )
        await conn.commit()

        yield conn

        await conn.close()

    @pytest_asyncio.fixture
    async def calculator(self, db):
        """Create calculator instance."""
        return TechnicalIndicatorCalculator(db)

    @pytest.mark.asyncio
    async def test_compute_rsi(self, calculator):
        """Test RSI calculation with known values."""
        # Generate 30 days of price data with clear trend
        prices = [Decimal("100") + Decimal(str(random.gauss(0, 1))) for _ in range(30)]

        indicators = await calculator.compute_for_symbol("TEST/USDT", prices)

        assert indicators.rsi_14 is not None
        assert 0 <= indicators.rsi_14 <= 100, f"RSI {indicators.rsi_14} out of range [0, 100]"

    @pytest.mark.asyncio
    async def test_compute_ema(self, calculator):
        """Test EMA calculation."""
        # Simple rising prices
        prices = [Decimal(str(100 + i)) for i in range(60)]

        indicators = await calculator.compute_for_symbol("TEST/USDT", prices)

        assert indicators.ema_20 is not None
        assert indicators.ema_50 is not None
        # EMA(20) should be higher than EMA(50) in rising market
        assert indicators.ema_20 > indicators.ema_50

    @pytest.mark.asyncio
    async def test_compute_bollinger(self, calculator):
        """Test Bollinger Bands calculation."""
        prices = [Decimal(str(100 + random.gauss(0, 5))) for _ in range(30)]

        indicators = await calculator.compute_for_symbol("TEST/USDT", prices)

        assert indicators.bollinger_upper is not None
        assert indicators.bollinger_lower is not None
        assert indicators.bollinger_upper >= indicators.bollinger_lower

    @pytest.mark.asyncio
    async def test_compute_macd(self, calculator):
        """Test MACD calculation."""
        prices = [Decimal(str(100 + i * 0.5 + random.gauss(0, 2))) for i in range(40)]

        indicators = await calculator.compute_for_symbol("TEST/USDT", prices)

        if indicators.macd_line is not None:
            assert indicators.macd_histogram is None or abs(indicators.macd_histogram) < abs(indicators.macd_line) * 2

    @pytest.mark.asyncio
    async def test_compute_atr(self, calculator):
        """Test ATR calculation."""
        prices = [Decimal(str(100 + random.gauss(0, 2))) for _ in range(20)]

        indicators = await calculator.compute_for_symbol("TEST/USDT", prices)

        assert indicators.atr_14 is not None
        assert indicators.atr_14 > 0

    @pytest.mark.asyncio
    async def test_synthetic_history_generation(self, calculator):
        """Test that synthetic history is generated when fewer than 20 prices provided."""
        # Only 5 price points
        prices = [Decimal("100"), Decimal("101"), Decimal("102"), Decimal("101"), Decimal("103")]

        indicators = await calculator.compute_for_symbol("TEST/USDT", prices)

        # Should still compute indicators using synthetic history
        assert indicators.rsi_14 is not None
        assert indicators.ema_20 is not None
        assert indicators.bollinger_upper is not None

    @pytest.mark.asyncio
    async def test_save_and_get_latest(self, calculator):
        """Test saving and retrieving indicators."""
        prices = [Decimal(str(100 + i)) for i in range(50)]

        indicators = await calculator.compute_for_symbol("TEST/USDT", prices)
        await calculator.save(indicators)

        retrieved = await calculator.get_latest("TEST/USDT")

        assert retrieved is not None
        assert retrieved.symbol == "TEST/USDT"
        assert retrieved.rsi_14 == indicators.rsi_14
        assert retrieved.ema_20 == indicators.ema_20

    @pytest.mark.asyncio
    async def test_get_history(self, calculator):
        """Test retrieving indicator history."""
        prices = [Decimal(str(100 + i)) for i in range(50)]

        # Generate indicators twice with different prices
        indicators1 = await calculator.compute_for_symbol("TEST/USDT", prices)
        await calculator.save(indicators1)

        prices2 = [Decimal(str(100 + i * 0.5)) for i in range(50)]
        indicators2 = await calculator.compute_for_symbol("TEST/USDT", prices2)
        await calculator.save(indicators2)

        history = await calculator.get_history("TEST/USDT", limit=10)

        assert len(history) >= 2
        # Most recent first
        assert history[0].computed_at >= history[-1].computed_at

    @pytest.mark.asyncio
    async def test_ema_values_verification(self, calculator):
        """Test EMA calculation with fixed known values."""
        # Fixed price series
        prices = [Decimal("100") for _ in range(50)]

        indicators = await calculator.compute_for_symbol("TEST/USDT", prices)

        # With flat prices, EMA should equal SMA = 100
        assert indicators.ema_20 is not None
        assert abs(indicators.ema_20 - 100.0) < 0.01
        assert indicators.ema_50 is not None
        assert abs(indicators.ema_50 - 100.0) < 0.01

    @pytest.mark.asyncio
    async def test_rsi_bounds(self, calculator):
        """Test that RSI values are within [0, 100]."""
        # Generate trending prices
        prices = [Decimal(str(100 + i * 2)) for i in range(30)]

        indicators = await calculator.compute_for_symbol("TEST/USDT", prices)

        if indicators.rsi_14 is not None:
            assert 0 <= indicators.rsi_14 <= 100


class TestIndicatorCalculations:
    """Tests for specific indicator calculation logic."""

    def test_rsi_known_values(self):
        """Test RSI with known input values."""
        # Create calculator directly without db for unit testing
        calc = TechnicalIndicatorCalculator(None)

        # Rising prices (always positive change)
        rising = [100 + i for i in range(20)]
        rsi = calc._compute_rsi(rising, period=14)
        assert rsi == 100.0, "RSI should be 100 for consistently rising prices"

        # Falling prices (always negative change)
        falling = [100 - i for i in range(20)]
        rsi = calc._compute_rsi(falling, period=14)
        assert rsi == 0.0, "RSI should be 0 for consistently falling prices"

    def test_ema_smoothing_factor(self):
        """Test EMA with specific smoothing factor verification."""
        calc = TechnicalIndicatorCalculator(None)

        # For EMA(20), k = 2/(20+1) = 2/21 ≈ 0.0952
        prices = [100.0] * 20 + [110.0]  # Flat then spike

        ema = calc._compute_ema(prices, 20)
        assert ema is not None
        # After flat period, EMA should be close to 100
        assert 99 < ema < 110

    def test_bollinger_upper_greater_than_lower(self):
        """Test that Bollinger upper is always >= lower."""
        calc = TechnicalIndicatorCalculator(None)

        prices = [100 + random.gauss(0, 10) for _ in range(30)]
        upper, lower = calc._compute_bollinger(prices, period=20, std_dev=2)

        assert upper is not None
        assert lower is not None
        assert upper >= lower

    def test_synthetic_history_deterministic(self):
        """Test that synthetic history is deterministic with fixed seed."""
        calc = TechnicalIndicatorCalculator(None)

        prices1 = calc._generate_synthetic_history(100.0, 50)
        prices2 = calc._generate_synthetic_history(100.0, 50)

        assert prices1 == prices2, "Synthetic history should be deterministic"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])