"""Registry for data source adapters with fallback support."""

import logging
from typing import Optional

from .cache import KlineCache
from .cryptocompare import CryptoCompareAdapter

logger = logging.getLogger(__name__)


class DataSourceRegistry:
    """Registry for kline data sources with fallback logic."""

    def __init__(
        self,
        cache: KlineCache,
        cryptocompare_adapter: CryptoCompareAdapter,
    ):
        """Initialize DataSourceRegistry.

        Args:
            cache: KlineCache instance for persistence.
            cryptocompare_adapter: CryptoCompareAdapter instance.
        """
        self._cache = cache
        self._cc_adapter = cryptocompare_adapter

    async def get_klines(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 500,
        force_refresh: bool = False,
    ) -> list[dict]:
        """Get klines for symbol/timeframe.

        Uses lazy loading:
        - Returns cached data if fresh
        - Fetches from CryptoCompare if stale or force_refresh

        Args:
            symbol: Trading symbol (e.g., 'BTC/USDT').
            timeframe: Timeframe (e.g., '1h').
            limit: Maximum candles to return.
            force_refresh: Force fetch from API even if cache is fresh.

        Returns:
            List of candle dicts.

        Raises:
            Exception if both cache and API fail.
        """
        # Try cache first (unless force_refresh)
        if not force_refresh:
            candles = self._cache.get_candles(symbol, timeframe)
            if candles is not None and not self._cache.is_stale(symbol, timeframe):
                logger.debug("Cache hit for %s %s", symbol, timeframe)
                return candles[-limit:] if len(candles) > limit else candles

        # Fetch from CryptoCompare
        try:
            candles = await self._cc_adapter.fetch_klines(symbol, timeframe, limit)
            self._cache.update_candles(symbol, timeframe, candles)
            logger.info("Fetched and cached %d candles for %s %s", len(candles), symbol, timeframe)
            return candles[-limit:] if len(candles) > limit else candles
        except Exception as e:
            logger.warning("CryptoCompare failed for %s: %s", symbol, e)

            # Fallback: try to return stale cache even if stale
            candles = self._cache.get_candles(symbol, timeframe)
            if candles is not None:
                logger.info("Returning stale cache for %s %s", symbol, timeframe)
                return candles[-limit:] if len(candles) > limit else candles

            # No cache available
            raise Exception(f"Failed to fetch klines for {symbol}: {e}") from e

    def get_cache_info(self) -> dict:
        """Get information about the cache.

        Returns:
            Dict with cache statistics: symbols, timeframes, staleness.
        """
        symbols = self._cache.get_all_symbols()
        info = {
            "symbol_count": len(symbols),
            "symbols": [],
        }
        for symbol in symbols:
            timeframes = list(self._cache._cache.get(symbol, {}).keys())
            staleness = {}
            for tf in timeframes:
                staleness[tf] = self._cache.is_stale(symbol, tf)
            info["symbols"].append({
                "symbol": symbol,
                "timeframes": timeframes,
                "is_stale": staleness,
            })
        return info