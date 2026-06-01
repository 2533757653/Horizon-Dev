"""Symbol cache management for PairList system."""

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..exchange.adapter import ExchangeAdapter
    import aiosqlite

logger = logging.getLogger(__name__)


class SymbolCache:
    """Manages the exchange_symbols cache table in the database."""

    def __init__(self, db: "aiosqlite.Connection") -> None:
        self._db = db

    async def refresh_for_exchange(self, adapter: "ExchangeAdapter") -> int:
        """Fetch all symbols from an exchange and refresh the cache.

        Deletes old entries for this exchange and inserts new ones.

        Args:
            adapter: The exchange adapter to fetch from.

        Returns:
            Number of symbols inserted.
        """
        symbols = await adapter.fetch_all_symbols()
        if not symbols:
            await self._db.execute("DELETE FROM exchange_symbols WHERE exchange = ?", (adapter.name,))
            await self._db.commit()
            return 0

        now_ms = int(time.time() * 1000)
        # Prepare batch data
        batch = [
            (
                adapter.name,
                sym.symbol,
                sym.base_asset,
                sym.quote_asset,
                str(sym.volume_24h) if sym.volume_24h is not None else None,
                str(sym.price) if sym.price is not None else None,
                now_ms,
            )
            for sym in symbols
        ]

        # Delete + batch insert in single transaction
        await self._db.execute("DELETE FROM exchange_symbols WHERE exchange = ?", (adapter.name,))
        await self._db.executemany(
            """INSERT INTO exchange_symbols
               (exchange, symbol, base_asset, quote_asset, volume_24h, price, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            batch,
        )
        await self._db.commit()
        logger.info("Refreshed %d symbols from %s", len(symbols), adapter.name)
        return len(symbols)

    async def get_all_pairs(self) -> list[str]:
        """Get all unique symbols from the cache (generic format)."""
        cursor = await self._db.execute(
            "SELECT DISTINCT symbol FROM exchange_symbols ORDER BY symbol"
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]