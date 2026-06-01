"""Top-coins PairList: returns top 50 symbols by 24h volume."""

import aiosqlite
from horizon.internal.pairlist.base import PairList


class TopCoinsPairList(PairList):
    @property
    def name(self) -> str:
        return "top_coins"

    @property
    def description(self) -> str:
        return "Top 50 coins by 24h volume across all exchanges"

    async def get_pairs(self, db: aiosqlite.Connection) -> list[str]:
        cursor = await db.execute(
            """
            SELECT symbol, MAX(CAST(volume_24h AS REAL)) AS max_vol
            FROM exchange_symbols
            WHERE volume_24h IS NOT NULL
            GROUP BY symbol
            ORDER BY max_vol DESC
            LIMIT 50
            """
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]