"""Technical indicator calculator for Horizon Trading Platform.

Computes RSI, MACD, EMA, Bollinger Bands, and ATR from price data.
"""

import random
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import aiosqlite


@dataclass
class TechnicalIndicators:
    """Data class containing computed technical indicators."""

    symbol: str
    rsi_14: Optional[float]
    macd_line: Optional[float]
    macd_signal: Optional[float]
    macd_histogram: Optional[float]
    ema_20: Optional[float]
    ema_50: Optional[float]
    bollinger_upper: Optional[float]
    bollinger_lower: Optional[float]
    atr_14: Optional[float]
    computed_at: datetime


class TechnicalIndicatorCalculator:
    """Computes technical indicators from price data."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        """Initialize the calculator with a database connection.

        Args:
            db: aiosqlite database connection.
        """
        self._db = db

    async def compute_for_symbol(
        self, symbol: str, prices: Optional[list[Decimal]] = None
    ) -> TechnicalIndicators:
        """Compute technical indicators for a symbol.

        If prices is None, queries market_data_cache for last 50 price records.
        If fewer than 20 price points are available, generates synthetic history.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT').
            prices: Optional list of Decimal price values. If None, fetches from DB.

        Returns:
            TechnicalIndicators with all computed values.
        """
        if prices is None:
            prices = await self._fetch_prices_from_db(symbol)

        # Convert to float list for computation
        price_values = [float(p) for p in prices]

        # Generate synthetic history if needed
        if len(price_values) < 20:
            latest_price = price_values[-1] if price_values else 100.0
            price_values = self._generate_synthetic_history(
                latest_price, max(50, 50 - len(price_values) + len(price_values))
            )

        # Compute all indicators
        rsi = self._compute_rsi(price_values, period=14)
        ema_20 = self._compute_ema(price_values, period=20)
        ema_50 = self._compute_ema(price_values, period=50)
        macd_line, macd_signal, macd_histogram = self._compute_macd(price_values)
        bollinger_upper, bollinger_lower = self._compute_bollinger(price_values, period=20, std_dev=2)
        atr = self._compute_atr(price_values, period=14)

        return TechnicalIndicators(
            symbol=symbol,
            rsi_14=rsi,
            macd_line=macd_line,
            macd_signal=macd_signal,
            macd_histogram=macd_histogram,
            ema_20=ema_20,
            ema_50=ema_50,
            bollinger_upper=bollinger_upper,
            bollinger_lower=bollinger_lower,
            atr_14=atr,
            computed_at=datetime.now(timezone.utc),
        )

    async def _fetch_prices_from_db(self, symbol: str) -> list[Decimal]:
        """Fetch price history from market_data_cache table.

        Args:
            symbol: Trading pair symbol.

        Returns:
            List of Decimal price values ordered by recorded_at DESC.
        """
        cursor = await self._db.execute(
            """
            SELECT last_price FROM market_data_cache
            WHERE symbol = ?
            ORDER BY recorded_at DESC
            LIMIT 50
            """,
            (symbol,),
        )
        rows = await cursor.fetchall()
        return [Decimal(row["last_price"]) for row in rows if row["last_price"]]

    def _generate_synthetic_history(self, latest_price: float, count: int) -> list[float]:
        """Generate synthetic price history using seeded random walk.

        Args:
            latest_price: Starting price for the random walk.
            count: Number of price points to generate.

        Returns:
            List of synthetic price values.
        """
        # Use fixed seed for deterministic results
        rng = random.Random(42)
        prices = [latest_price]
        for _ in range(count - 1):
            # Random walk with mean 0 and std dev of 1% of price
            change_pct = rng.gauss(0, 0.01)
            new_price = prices[-1] * (1 + change_pct)
            prices.append(new_price)
        return prices

    def _compute_rsi(self, prices: list[float], period: int = 14) -> Optional[float]:
        """Compute RSI using Wilder's smoothing method.

        Args:
            prices: List of price values.
            period: RSI period (default 14).

        Returns:
            RSI value between 0 and 100, or None if insufficient data.
        """
        if len(prices) < period + 1:
            return None

        # Calculate price changes
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

        # Separate gains and losses
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        # Initial average (simple average for first period)
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Wilder's smoothing for subsequent values
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def _compute_ema(self, prices: list[float], period: int) -> Optional[float]:
        """Compute Exponential Moving Average.

        Args:
            prices: List of price values.
            period: EMA period.

        Returns:
            EMA value or None if insufficient data.
        """
        if len(prices) < period:
            return None

        # Smoothing factor k = 2 / (N + 1)
        k = 2 / (period + 1)

        # Start with SMA for first period
        ema = sum(prices[:period]) / period

        # Apply EMA formula for subsequent values
        for price in prices[period:]:
            ema = price * k + ema * (1 - k)

        return ema

    def _compute_macd(
        self, prices: list[float]
    ) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """Compute MACD indicator.

        Args:
            prices: List of price values.

        Returns:
            Tuple of (MACD line, MACD signal, MACD histogram) or (None, None, None).
        """
        if len(prices) < 26:
            return None, None, None

        # Calculate MACD line = EMA(12) - EMA(26)
        ema_12 = self._compute_ema(prices, 12)
        ema_26 = self._compute_ema(prices, 26)

        if ema_12 is None or ema_26 is None:
            return None, None, None

        macd_line = ema_12 - ema_26

        # Calculate MACD signal = EMA(9) of MACD line
        # Build MACD line series
        macd_series = []
        for i in range(25, len(prices)):
            e12 = self._compute_ema(prices[: i + 1], 12)
            e26 = self._compute_ema(prices[: i + 1], 26)
            if e12 is not None and e26 is not None:
                macd_series.append(e12 - e26)

        if len(macd_series) < 9:
            return macd_line, None, None

        macd_signal = self._compute_ema(macd_series, 9)
        macd_histogram = macd_line - macd_signal if macd_signal is not None else None

        return macd_line, macd_signal, macd_histogram

    def _compute_bollinger(
        self, prices: list[float], period: int = 20, std_dev: float = 2
    ) -> tuple[Optional[float], Optional[float]]:
        """Compute Bollinger Bands.

        Args:
            prices: List of price values.
            period: SMA period (default 20).
            std_dev: Number of standard deviations (default 2).

        Returns:
            Tuple of (upper band, lower band) or (None, None).
        """
        if len(prices) < period:
            return None, None

        # Calculate SMA
        sma = sum(prices[-period:]) / period

        # Calculate standard deviation
        variance = sum((p - sma) ** 2 for p in prices[-period:]) / period
        std = math.sqrt(variance)

        upper = sma + std_dev * std
        lower = sma - std_dev * std

        return upper, lower

    def _compute_atr(self, prices: list[float], period: int = 14) -> Optional[float]:
        """Compute ATR (Average True Range) using simplified price range method.

        Args:
            prices: List of price values.
            period: ATR period (default 14).

        Returns:
            ATR value or None if insufficient data.
        """
        if len(prices) < period + 1:
            return None

        # Simplified True Range = abs(price[t] - price[t-1])
        tr_values = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]

        # Initial ATR as simple average
        atr = sum(tr_values[:period]) / period

        # Wilder's smoothing for subsequent values
        for i in range(period, len(tr_values)):
            atr = (atr * (period - 1) + tr_values[i]) / period

        return atr

    async def save(self, indicators: TechnicalIndicators) -> None:
        """Save indicators to the technical_indicators table.

        Args:
            indicators: TechnicalIndicators object to save.
        """
        try:
            await self._db.execute(
                """
                INSERT INTO technical_indicators
                (symbol, rsi_14, macd_line, macd_signal, macd_histogram,
                 ema_20, ema_50, bollinger_upper, bollinger_lower, atr_14, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    indicators.symbol,
                    indicators.rsi_14,
                    indicators.macd_line,
                    indicators.macd_signal,
                    indicators.macd_histogram,
                    indicators.ema_20,
                    indicators.ema_50,
                    indicators.bollinger_upper,
                    indicators.bollinger_lower,
                    indicators.atr_14,
                    indicators.computed_at.isoformat(),
                ),
            )
            await self._db.commit()
        except aiosqlite.IntegrityError:
            # Update existing row
            await self._db.execute(
                """
                UPDATE technical_indicators
                SET rsi_14 = ?, macd_line = ?, macd_signal = ?, macd_histogram = ?,
                    ema_20 = ?, ema_50 = ?, bollinger_upper = ?, bollinger_lower = ?,
                    atr_14 = ?
                WHERE symbol = ? AND computed_at = ?
                """,
                (
                    indicators.rsi_14,
                    indicators.macd_line,
                    indicators.macd_signal,
                    indicators.macd_histogram,
                    indicators.ema_20,
                    indicators.ema_50,
                    indicators.bollinger_upper,
                    indicators.bollinger_lower,
                    indicators.atr_14,
                    indicators.symbol,
                    indicators.computed_at.isoformat(),
                ),
            )
            await self._db.commit()

    async def get_latest(self, symbol: str) -> Optional[TechnicalIndicators]:
        """Get the most recent indicators for a symbol.

        Args:
            symbol: Trading pair symbol.

        Returns:
            TechnicalIndicators or None if not found.
        """
        cursor = await self._db.execute(
            """
            SELECT symbol, rsi_14, macd_line, macd_signal, macd_histogram,
                   ema_20, ema_50, bollinger_upper, bollinger_lower, atr_14, computed_at
            FROM technical_indicators
            WHERE symbol = ?
            ORDER BY computed_at DESC
            LIMIT 1
            """,
            (symbol,),
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        return TechnicalIndicators(
            symbol=row["symbol"],
            rsi_14=row["rsi_14"],
            macd_line=row["macd_line"],
            macd_signal=row["macd_signal"],
            macd_histogram=row["macd_histogram"],
            ema_20=row["ema_20"],
            ema_50=row["ema_50"],
            bollinger_upper=row["bollinger_upper"],
            bollinger_lower=row["bollinger_lower"],
            atr_14=row["atr_14"],
            computed_at=datetime.fromisoformat(row["computed_at"]),
        )

    async def get_history(
        self, symbol: str, limit: int = 100
    ) -> list[TechnicalIndicators]:
        """Get historical indicators for a symbol.

        Args:
            symbol: Trading pair symbol.
            limit: Maximum number of records to return (default 100).

        Returns:
            List of TechnicalIndicators ordered by computed_at DESC.
        """
        cursor = await self._db.execute(
            """
            SELECT symbol, rsi_14, macd_line, macd_signal, macd_histogram,
                   ema_20, ema_50, bollinger_upper, bollinger_lower, atr_14, computed_at
            FROM technical_indicators
            WHERE symbol = ?
            ORDER BY computed_at DESC
            LIMIT ?
            """,
            (symbol, limit),
        )
        rows = await cursor.fetchall()

        return [
            TechnicalIndicators(
                symbol=row["symbol"],
                rsi_14=row["rsi_14"],
                macd_line=row["macd_line"],
                macd_signal=row["macd_signal"],
                macd_histogram=row["macd_histogram"],
                ema_20=row["ema_20"],
                ema_50=row["ema_50"],
                bollinger_upper=row["bollinger_upper"],
                bollinger_lower=row["bollinger_lower"],
                atr_14=row["atr_14"],
                computed_at=datetime.fromisoformat(row["computed_at"]),
            )
            for row in rows
        ]