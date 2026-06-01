"""All-symbols PairList: returns every cached symbol, no filtering."""

import aiosqlite
from horizon.internal.pairlist.base import PairList


class AllSymbolsPairList(PairList):
    @property
    def name(self) -> str:
        return "all_symbols"

    @property
    def description(self) -> str:
        return "All cached symbols from active exchanges"

    async def get_pairs(self, db: aiosqlite.Connection) -> list[str]:
        cursor = await db.execute(
            "SELECT DISTINCT symbol FROM exchange_symbols ORDER BY symbol"
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]