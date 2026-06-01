"""USDT-only PairList: all symbols quoted in USDT."""

import aiosqlite
from horizon.internal.pairlist.base import PairList


class UsdtOnlyPairList(PairList):
    @property
    def name(self) -> str:
        return "usdt_only"

    @property
    def description(self) -> str:
        return "All symbols quoted in USDT"

    async def get_pairs(self, db: aiosqlite.Connection) -> list[str]:
        cursor = await db.execute(
            "SELECT DISTINCT symbol FROM exchange_symbols WHERE quote_asset = 'USDT' ORDER BY symbol"
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]