"""Kline cache with pickle persistence and lazy loading."""

import logging
import os
import pickle
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class KlineCache:
    """Manages in-memory Kline cache with pickle persistence.

    Data structure:
        _cache = {
            "BTC/USDT": {
                "1h": [{time, open, high, low, close, volume}, ...],
                "4h": [...],
            },
            "ETH/USDT": {...},
        }
        _timestamps = {
            "BTC/USDT": {"1h": last_fetch_time, "4h": last_fetch_time},
        }
    """

    DEFAULT_STALE_SECONDS = 3600  # 1 hour

    def __init__(
        self,
        cache_file: str = "./data/kline_cache.pkl",
        stale_seconds: int = DEFAULT_STALE_SECONDS,
    ):
        """Initialize KlineCache.

        Args:
            cache_file: Path to pickle file for persistence.
            stale_seconds: Time in seconds before cache is considered stale.
        """
        self._cache_file = Path(cache_file)
        self._stale_seconds = stale_seconds
        self._cache: dict[str, dict[str, list]] = {}
        self._timestamps: dict[str, dict[str, float]] = {}

    def _ensure_dir(self) -> None:
        """Create cache directory if not exists."""
        parent = self._cache_file.parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> None:
        """Load cache from pickle file."""
        if not self._cache_file.exists():
            logger.info("Kline cache file not found, starting fresh")
            return

        try:
            with open(self._cache_file, "rb") as f:
                data = pickle.load(f)
            self._cache = data.get("cache", {})
            self._timestamps = data.get("timestamps", {})
            logger.info(
                "Kline cache loaded from %s: %d symbols",
                self._cache_file,
                len(self._cache),
            )
        except Exception as e:
            logger.warning("Failed to load kline cache: %s", e)
            self._cache = {}
            self._timestamps = {}

    def save(self) -> None:
        """Save cache to pickle file."""
        self._ensure_dir()
        try:
            data = {
                "cache": self._cache,
                "timestamps": self._timestamps,
            }
            with open(self._cache_file, "wb") as f:
                pickle.dump(data, f)
            logger.debug("Kline cache saved to %s", self._cache_file)
        except Exception as e:
            logger.warning("Failed to save kline cache: %s", e)

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
    ) -> Optional[list]:
        """Get cached candles for symbol/timeframe.

        Args:
            symbol: Trading symbol (e.g., 'BTC/USDT').
            timeframe: Timeframe (e.g., '1h').

        Returns:
            List of candle dicts or None if not cached.
        """
        return self._cache.get(symbol, {}).get(timeframe)

    def is_stale(self, symbol: str, timeframe: str) -> bool:
        """Check if cache for symbol/timeframe is stale.

        Args:
            symbol: Trading symbol.
            timeframe: Timeframe.

        Returns:
            True if cache is stale or doesn't exist.
        """
        last_fetch = self._timestamps.get(symbol, {}).get(timeframe, 0)
        return (time.time() - last_fetch) > self._stale_seconds

    def update_candles(
        self,
        symbol: str,
        timeframe: str,
        candles: list,
    ) -> None:
        """Update cache with new candles.

        Args:
            symbol: Trading symbol.
            timeframe: Timeframe.
            candles: List of candle dicts.
        """
        if symbol not in self._cache:
            self._cache[symbol] = {}
        if symbol not in self._timestamps:
            self._timestamps[symbol] = {}

        self._cache[symbol][timeframe] = candles
        self._timestamps[symbol][timeframe] = time.time()
        self.save()

    def get_all_symbols(self) -> list[str]:
        """Get list of all cached symbols."""
        return list(self._cache.keys())