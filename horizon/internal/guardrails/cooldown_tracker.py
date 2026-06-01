"""Cooldown Tracker for guardrail enforcement.

Tracks cooldown periods per exchange/symbol to prevent rapid re-trading.
"""

import logging
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger(__name__)


class CooldownTracker:
    """Tracks cooldown periods per exchange/symbol after trades."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        """Initialize the CooldownTracker.

        Args:
            db: Async SQLite database connection.
        """
        self._db = db

    async def record_trade(
        self, exchange: str, symbol: str, cooldown_seconds: int
    ) -> None:
        """Record a trade and start the cooldown period.

        Args:
            exchange: Exchange name.
            symbol: Trading pair symbol.
            cooldown_seconds: Cooldown duration in seconds.
        """
        now = datetime.now(timezone.utc)
        await self._db.execute(
            """
            INSERT INTO cooldown_tracking (exchange, symbol, last_trade_at, cooldown_seconds)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(exchange, symbol) DO UPDATE SET
                last_trade_at = excluded.last_trade_at,
                cooldown_seconds = excluded.cooldown_seconds
            """,
            (exchange, symbol, now, cooldown_seconds),
        )
        await self._db.commit()
        logger.debug(
            f"Recorded trade cooldown: {exchange} {symbol} for {cooldown_seconds}s"
        )

    async def is_in_cooldown(
        self, exchange: str, symbol: str
    ) -> tuple[bool, int | None]:
        """Check if an exchange/symbol pair is in cooldown.

        Args:
            exchange: Exchange name.
            symbol: Trading pair symbol.

        Returns:
            Tuple of (is_in_cooldown: bool, remaining_seconds: int | None).
            If in cooldown, returns (True, remaining_seconds).
            If not in cooldown, returns (False, None).
        """
        cursor = await self._db.execute(
            """
            SELECT last_trade_at, cooldown_seconds
            FROM cooldown_tracking
            WHERE exchange = ? AND symbol = ?
            """,
            (exchange, symbol),
        )
        row = await cursor.fetchone()

        if row is None or row["last_trade_at"] is None:
            return False, None

        last_trade_at = row["last_trade_at"]
        cooldown_seconds = row["cooldown_seconds"]

        # Parse datetime if string
        if isinstance(last_trade_at, str):
            last_trade_at = datetime.fromisoformat(
                last_trade_at.replace("Z", "+00:00")
            )

        elapsed = (datetime.now(timezone.utc) - last_trade_at).total_seconds()
        remaining = cooldown_seconds - elapsed

        if remaining > 0:
            return True, int(remaining)
        return False, None

    async def get_all_cooldowns(self) -> list[dict]:
        """Get all active cooldowns with remaining seconds computed.

        Returns:
            List of cooldown dictionaries with exchange, symbol,
            remaining_seconds, and last_trade_at.
        """
        cursor = await self._db.execute(
            """
            SELECT exchange, symbol, last_trade_at, cooldown_seconds
            FROM cooldown_tracking
            """
        )
        rows = await cursor.fetchall()

        cooldowns = []
        now = datetime.now(timezone.utc)

        for row in rows:
            last_trade_at = row["last_trade_at"]
            cooldown_seconds = row["cooldown_seconds"]

            # Parse datetime if string
            if isinstance(last_trade_at, str):
                last_trade_at = datetime.fromisoformat(
                    last_trade_at.replace("Z", "+00:00")
                )

            elapsed = (now - last_trade_at).total_seconds()
            remaining = cooldown_seconds - elapsed

            cooldowns.append(
                {
                    "exchange": row["exchange"],
                    "symbol": row["symbol"],
                    "remaining_seconds": int(remaining) if remaining > 0 else 0,
                    "last_trade_at": last_trade_at,
                }
            )

        return cooldowns

    async def cleanup_expired(self) -> None:
        """Delete rows where cooldown has expired."""
        now = datetime.now(timezone.utc)
        cursor = await self._db.execute(
            """
            SELECT exchange, symbol, last_trade_at, cooldown_seconds
            FROM cooldown_tracking
            """
        )
        rows = await cursor.fetchall()

        for row in rows:
            last_trade_at = row["last_trade_at"]
            cooldown_seconds = row["cooldown_seconds"]

            if isinstance(last_trade_at, str):
                last_trade_at = datetime.fromisoformat(
                    last_trade_at.replace("Z", "+00:00")
                )

            elapsed = (now - last_trade_at).total_seconds()
            if elapsed >= cooldown_seconds:
                await self._db.execute(
                    """
                    DELETE FROM cooldown_tracking
                    WHERE exchange = ? AND symbol = ?
                    """,
                    (row["exchange"], row["symbol"]),
                )

        await self._db.commit()
        logger.debug("Cleaned up expired cooldowns")