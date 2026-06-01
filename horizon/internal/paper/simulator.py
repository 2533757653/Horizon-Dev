"""Paper Trading Simulator for Horizon Trading Platform.

Provides async market and limit order simulation with P&L tracking.
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from ..marketdata.fetcher import MarketDataFetcher


logger = logging.getLogger(__name__)


class PaperTradeError(Exception):
    """Exception raised for paper trading errors."""
    pass


@dataclass
class PaperTradeResult:
    """Result of a paper trade operation.

    Attributes:
        order_id: Unique identifier for the paper trade.
        exchange: Exchange name.
        symbol: Trading pair symbol.
        side: Trade side (buy/sell).
        order_type: Order type (market/limit).
        price: Execution price.
        volume: Trade volume.
        notional_value: Total notional value (price * volume).
        status: Trade status (filled/pending/cancelled).
        paper_pnl: Realized P&L from closing a position.
    """
    order_id: str
    exchange: str
    symbol: str
    side: str
    order_type: str
    price: Decimal
    volume: Decimal
    notional_value: Decimal
    status: str
    paper_pnl: Decimal = Decimal("0")


def _generate_order_id() -> str:
    """Generate a unique paper trade order ID.

    Returns:
        Unique order ID in format "PAPER-{timestamp}-{random}".
    """
    return f"PAPER-{int(time.time() * 1000)}-{random.randint(1000, 9999)}"


class PaperTradingSimulator:
    """Simulates market and limit orders for paper trading.

    Manages paper positions, calculates P&L, and updates daily summaries.
    All operations use the fetcher for market data (no real exchange calls).
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        fetcher: "MarketDataFetcher",
    ) -> None:
        """Initialize the paper trading simulator.

        Args:
            db: Async SQLite database connection.
            fetcher: MarketDataFetcher instance for fetching market prices.
        """
        self._db = db
        self._fetcher = fetcher

    async def simulate_market_order(
        self,
        proposal_id: str | None,
        exchange: str,
        symbol: str,
        side: str,
        volume: Decimal,
    ) -> PaperTradeResult:
        """Simulate a market order execution.

        Fetches the current ticker, uses ask price for buys and bid price for sells.
        Updates paper_positions and daily_pnl accordingly.

        Args:
            proposal_id: Optional proposal ID that triggered this trade.
            exchange: Exchange name.
            symbol: Trading pair symbol (e.g., 'BTC/USDT').
            side: Trade side ('buy' or 'sell').
            volume: Trade volume.

        Returns:
            PaperTradeResult with execution details.

        Raises:
            PaperTradeError: If market data is unavailable.
        """
        # Fetch ticker for market price
        ticker = self._fetcher.get_ticker(symbol, exchange)
        if ticker is None:
            raise PaperTradeError(f"No ticker available for {symbol} on {exchange}")

        # Use ask for buy, bid for sell
        if side == "buy":
            price = ticker.price
        else:
            price = ticker.price

        notional = price * volume
        order_id = _generate_order_id()
        now = datetime.now(timezone.utc)

        # Insert into paper_trades
        await self._db.execute(
            """
            INSERT INTO paper_trades (
                id, proposal_id, exchange, symbol, side, order_type,
                price, volume, notional_value, status, filled_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                proposal_id,
                exchange,
                symbol,
                side,
                "market",
                float(price),
                float(volume),
                float(notional),
                "filled",
                now,
                now,
            ),
        )

        # Update or insert paper_positions
        await self._update_position(
            exchange=exchange,
            symbol=symbol,
            side=side,
            volume=float(volume),
            entry_price=float(price),
            current_price=float(price),
        )

        # Update daily_pnl with paper trade value
        await self._update_daily_pnl(paper_pnl=float(notional))

        await self._db.commit()

        return PaperTradeResult(
            order_id=order_id,
            exchange=exchange,
            symbol=symbol,
            side=side,
            order_type="market",
            price=price,
            volume=volume,
            notional_value=notional,
            status="filled",
            paper_pnl=Decimal("0"),
        )

    async def simulate_limit_order(
        self,
        proposal_id: str | None,
        exchange: str,
        symbol: str,
        side: str,
        price: Decimal,
        volume: Decimal,
    ) -> PaperTradeResult:
        """Simulate a limit order execution.

        Validates that buy limits are <= market * 1.02 and sell limits >= market * 0.98.
        Executes at the limit price if validation passes.

        Args:
            proposal_id: Optional proposal ID that triggered this trade.
            exchange: Exchange name.
            symbol: Trading pair symbol (e.g., 'BTC/USDT').
            side: Trade side ('buy' or 'sell').
            price: Limit price.
            volume: Trade volume.

        Returns:
            PaperTradeResult with execution details.

        Raises:
            PaperTradeError: If price validation fails or market data unavailable.
        """
        # Fetch ticker for market price validation
        ticker = self._fetcher.get_ticker(symbol, exchange)
        if ticker is None:
            raise PaperTradeError(f"No ticker available for {symbol} on {exchange}")

        market_price = ticker.price
        notional = price * volume

        # Validate limit price is within 2% of market
        if side == "buy":
            max_price = market_price * Decimal("1.02")
            if price > max_price:
                raise PaperTradeError(
                    f"Buy limit price {price} exceeds 2% above market {market_price} "
                    f"(max: {max_price})"
                )
        else:  # sell
            min_price = market_price * Decimal("0.98")
            if price < min_price:
                raise PaperTradeError(
                    f"Sell limit price {price} below 98% of market {market_price} "
                    f"(min: {min_price})"
                )

        order_id = _generate_order_id()
        now = datetime.now(timezone.utc)

        # Insert into paper_trades
        await self._db.execute(
            """
            INSERT INTO paper_trades (
                id, proposal_id, exchange, symbol, side, order_type,
                price, volume, notional_value, status, filled_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                proposal_id,
                exchange,
                symbol,
                side,
                "limit",
                float(price),
                float(volume),
                float(notional),
                "filled",
                now,
                now,
            ),
        )

        # Update or insert paper_positions
        await self._update_position(
            exchange=exchange,
            symbol=symbol,
            side=side,
            volume=float(volume),
            entry_price=float(price),
            current_price=float(price),
        )

        # Update daily_pnl with paper trade value
        await self._update_daily_pnl(paper_pnl=float(notional))

        await self._db.commit()

        return PaperTradeResult(
            order_id=order_id,
            exchange=exchange,
            symbol=symbol,
            side=side,
            order_type="limit",
            price=price,
            volume=volume,
            notional_value=notional,
            status="filled",
            paper_pnl=Decimal("0"),
        )

    async def close_paper_position(
        self,
        exchange: str,
        symbol: str,
        side: str,
        volume: Decimal | None = None,
    ) -> PaperTradeResult:
        """Close (or partially close) a paper position.

        Looks up the existing position, computes P&L based on side and volume,
        updates or deletes the position, and inserts a closing trade record.

        Args:
            exchange: Exchange name.
            symbol: Trading pair symbol (e.g., 'BTC/USDT').
            side: Side to close ('sell' to close long, 'buy' to close short).
            volume: Volume to close (None = close entire position).

        Returns:
            PaperTradeResult with closing details and P&L.

        Raises:
            PaperTradeError: If no position found or insufficient volume.
        """
        # Fetch current ticker for closing price
        ticker = self._fetcher.get_ticker(symbol, exchange)
        if ticker is None:
            raise PaperTradeError(f"No ticker available for {symbol} on {exchange}")

        close_price = ticker.price

        # To close a position: sell to close long (buy side), buy to close short (sell side)
        # So we look up the position by the OPPOSITE of the close side
        position_side = "buy" if side == "sell" else "sell"

        # Look up existing position
        cursor = await self._db.execute(
            """
            SELECT id, volume, avg_entry_price, side
            FROM paper_positions
            WHERE exchange = ? AND symbol = ? AND side = ?
            """,
            (exchange, symbol, position_side),
        )
        row = await cursor.fetchone()

        if row is None:
            raise PaperTradeError(
                f"No {position_side} position found for {symbol} on {exchange}"
            )

        position_id = row["id"]
        position_volume = Decimal(str(row["volume"]))
        entry_price = Decimal(str(row["avg_entry_price"]))
        position_side = row["side"]

        # Determine close volume
        if volume is None:
            close_volume = position_volume
        else:
            close_volume = min(volume, position_volume)

        if close_volume > position_volume:
            raise PaperTradeError(
                f"Close volume {close_volume} exceeds position volume {position_volume}"
            )

        # Calculate P&L
        # Long position: profit when close price > entry price
        # Short position: profit when close price < entry price
        if position_side == "buy":
            pnl = (close_price - entry_price) * close_volume
        else:  # sell (short)
            pnl = (entry_price - close_price) * close_volume

        order_id = _generate_order_id()
        notional = close_price * close_volume
        now = datetime.now(timezone.utc)

        # Insert closing trade record
        await self._db.execute(
            """
            INSERT INTO paper_trades (
                id, exchange, symbol, side, order_type,
                price, volume, notional_value, status, filled_at,
                paper_pnl, closed_by_side, closed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                exchange,
                symbol,
                side,
                "market",  # close is executed at market price
                float(close_price),
                float(close_volume),
                float(notional),
                "filled",
                now,
                float(pnl),
                side,
                now,
                now,
            ),
        )

        # Update or delete position
        remaining_volume = position_volume - close_volume
        if remaining_volume <= Decimal("0"):
            # Close entire position
            await self._db.execute(
                "DELETE FROM paper_positions WHERE id = ?",
                (position_id,),
            )
        else:
            # Partial close - update volume
            # For partial close, we reduce volume but keep avg_entry_price the same
            await self._db.execute(
                """
                UPDATE paper_positions
                SET volume = ?, unrealized_pnl = ?
                WHERE id = ?
                """,
                (
                    float(remaining_volume),
                    float(Decimal("0")),  # Reset unrealized P&L
                    position_id,
                ),
            )

        # Update daily_pnl with realized P&L (negative of notional = cost basis)
        # P&L is added (pnl can be positive or negative)
        await self._update_daily_pnl(paper_pnl=float(pnl))

        await self._db.commit()

        return PaperTradeResult(
            order_id=order_id,
            exchange=exchange,
            symbol=symbol,
            side=side,
            order_type="market",
            price=close_price,
            volume=close_volume,
            notional_value=notional,
            status="filled",
            paper_pnl=pnl,
        )

    async def update_market_prices(self) -> None:
        """Background task to update all paper positions with current market prices.

        Fetches current tickers for all open positions and updates their
        current_price and unrealized_pnl in the database.
        """
        # Get all open positions
        cursor = await self._db.execute(
            "SELECT exchange, symbol, side, volume, avg_entry_price FROM paper_positions"
        )
        rows = await cursor.fetchall()

        for row in rows:
            exchange = row["exchange"]
            symbol = row["symbol"]
            side = row["side"]
            volume = Decimal(str(row["volume"]))
            entry_price = Decimal(str(row["avg_entry_price"]))

            # Fetch current ticker
            ticker = self._fetcher.get_ticker(symbol, exchange)
            if ticker is None:
                logger.warning(
                    f"No ticker for {symbol} on {exchange}, skipping price update"
                )
                continue

            current_price = ticker.price

            # Calculate unrealized P&L
            if side == "buy":
                unrealized_pnl = (current_price - entry_price) * volume
            else:  # sell (short)
                unrealized_pnl = (entry_price - current_price) * volume

            # Update position
            await self._db.execute(
                """
                UPDATE paper_positions
                SET current_price = ?, unrealized_pnl = ?
                WHERE exchange = ? AND symbol = ? AND side = ?
                """,
                (
                    float(current_price),
                    float(unrealized_pnl),
                    exchange,
                    symbol,
                    side,
                ),
            )

        await self._db.commit()
        logger.debug(f"Updated market prices for {len(rows)} paper positions")

    async def get_paper_positions(self) -> list[dict]:
        """Get all paper trading positions with unrealized P&L.

        Returns:
            List of position dicts with keys:
            id, exchange, symbol, side, volume, avg_entry_price,
            current_price, unrealized_pnl, opened_at.
        """
        cursor = await self._db.execute(
            """
            SELECT id, exchange, symbol, side, volume, avg_entry_price,
                   current_price, unrealized_pnl, opened_at
            FROM paper_positions
            ORDER BY opened_at DESC
            """
        )
        rows = await cursor.fetchall()

        positions = []
        for row in rows:
            positions.append({
                "id": row["id"],
                "exchange": row["exchange"],
                "symbol": row["symbol"],
                "side": row["side"],
                "volume": row["volume"],
                "avg_entry_price": row["avg_entry_price"],
                "current_price": row["current_price"],
                "unrealized_pnl": row["unrealized_pnl"],
                "opened_at": row["opened_at"],
            })

        return positions

    async def get_paper_pnl_summary(self) -> dict:
        """Get summary of paper trading P&L.

        Returns:
            Dict with:
            - total_realized_pnl: Sum of realized P&L from closed trades
            - total_unrealized_pnl: Sum of unrealized P&L from open positions
            - open_positions_count: Number of open positions
            - trades_count: Total number of paper trades
        """
        # Get realized P&L from closed trades (paper_pnl where closed_by_side is not null)
        cursor = await self._db.execute(
            "SELECT COALESCE(SUM(paper_pnl), 0.0) as realized FROM paper_trades WHERE closed_by_side IS NOT NULL"
        )
        realized_row = await cursor.fetchone()
        total_realized_pnl = realized_row["realized"] if realized_row else 0.0

        # Get unrealized P&L from open positions
        cursor = await self._db.execute(
            "SELECT COALESCE(SUM(unrealized_pnl), 0.0) as unrealized FROM paper_positions"
        )
        unrealized_row = await cursor.fetchone()
        total_unrealized_pnl = unrealized_row["unrealized"] if unrealized_row else 0.0

        # Get open positions count
        cursor = await self._db.execute("SELECT COUNT(*) as count FROM paper_positions")
        count_row = await cursor.fetchone()
        open_positions_count = count_row["count"] if count_row else 0

        # Get total trades count
        cursor = await self._db.execute("SELECT COUNT(*) as count FROM paper_trades")
        trades_row = await cursor.fetchone()
        trades_count = trades_row["count"] if trades_row else 0

        return {
            "total_realized_pnl": total_realized_pnl,
            "total_unrealized_pnl": total_unrealized_pnl,
            "open_positions_count": open_positions_count,
            "trades_count": trades_count,
        }

    async def _update_position(
        self,
        exchange: str,
        symbol: str,
        side: str,
        volume: float,
        entry_price: float,
        current_price: float,
    ) -> None:
        """Update or insert a paper position with weighted average entry price.

        For existing positions in the same direction, uses weighted average.
        For opposite direction, reduces or reverses the position.

        Args:
            exchange: Exchange name.
            symbol: Trading pair symbol.
            side: Position side ('buy' or 'sell').
            volume: Volume to add.
            entry_price: Entry price for the new volume.
            current_price: Current market price.
        """
        # Check for existing position
        cursor = await self._db.execute(
            """
            SELECT id, volume, avg_entry_price
            FROM paper_positions
            WHERE exchange = ? AND symbol = ? AND side = ?
            """,
            (exchange, symbol, side),
        )
        row = await cursor.fetchone()

        if row is None:
            # Insert new position
            unrealized_pnl = self._calc_unrealized_pnl(side, volume, entry_price, current_price)
            await self._db.execute(
                """
                INSERT INTO paper_positions (
                    exchange, symbol, side, volume, avg_entry_price,
                    current_price, unrealized_pnl
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exchange,
                    symbol,
                    side,
                    volume,
                    entry_price,
                    current_price,
                    unrealized_pnl,
                ),
            )
        else:
            # Update existing - calculate weighted average
            existing_volume = row["volume"]
            existing_entry = row["avg_entry_price"]
            new_total_volume = existing_volume + volume

            if new_total_volume <= 0:
                # Position would be closed or reversed
                await self._db.execute(
                    "DELETE FROM paper_positions WHERE id = ?",
                    (row["id"],),
                )
            else:
                # Weighted average entry price
                weighted_entry = (
                    (existing_volume * existing_entry + volume * entry_price)
                    / new_total_volume
                )
                unrealized_pnl = self._calc_unrealized_pnl(
                    side, new_total_volume, weighted_entry, current_price
                )
                await self._db.execute(
                    """
                    UPDATE paper_positions
                    SET volume = ?, avg_entry_price = ?, current_price = ?, unrealized_pnl = ?
                    WHERE id = ?
                    """,
                    (
                        new_total_volume,
                        weighted_entry,
                        current_price,
                        unrealized_pnl,
                        row["id"],
                    ),
                )

    def _calc_unrealized_pnl(
        self,
        side: str,
        volume: float,
        entry_price: float,
        current_price: float,
    ) -> float:
        """Calculate unrealized P&L for a position.

        Args:
            side: Position side ('buy' or 'sell').
            volume: Position volume.
            entry_price: Average entry price.
            current_price: Current market price.

        Returns:
            Unrealized P&L.
        """
        if side == "buy":
            return (current_price - entry_price) * volume
        else:  # sell (short)
            return (entry_price - current_price) * volume

    async def _update_daily_pnl(
        self,
        realized_pnl: float = 0.0,
        paper_pnl: float = 0.0,
    ) -> None:
        """Update or insert daily P&L record.

        Args:
            realized_pnl: Realized P&L to add.
            paper_pnl: Paper P&L to add.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now = datetime.now(timezone.utc)

        # Try to update existing record
        cursor = await self._db.execute(
            "SELECT id, realized_pnl, paper_pnl FROM daily_pnl WHERE date = ?",
            (today,),
        )
        row = await cursor.fetchone()

        if row is None:
            # Insert new record
            await self._db.execute(
                """
                INSERT INTO daily_pnl (
                    date, realized_pnl, paper_pnl, autonomous_trades,
                    manual_trades, llm_proposals, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    today,
                    realized_pnl,
                    paper_pnl,
                    0,
                    0,
                    0,
                    now,
                ),
            )
        else:
            # Update existing
            new_realized = (row["realized_pnl"] or 0.0) + realized_pnl
            new_paper = (row["paper_pnl"] or 0.0) + paper_pnl
            await self._db.execute(
                """
                UPDATE daily_pnl
                SET realized_pnl = ?, paper_pnl = ?, updated_at = ?
                WHERE date = ?
                """,
                (new_realized, new_paper, now, today),
            )