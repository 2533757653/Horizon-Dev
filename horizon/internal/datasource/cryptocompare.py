"""CryptoCompare API adapter for Kline data."""

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# CryptoCompare doesn't require API key for basic endpoints
DEFAULT_BASE_URL = "https://min-api.cryptocompare.com"


class CryptoCompareAdapter:
    """Fetches OHLCV klines from CryptoCompare API."""

    TIMEOUT_SECONDS = 30

    def __init__(self, api_key: str = "", base_url: str = DEFAULT_BASE_URL):
        """Initialize CryptoCompare adapter.

        Args:
            api_key: Optional API key for higher rate limits.
            base_url: CryptoCompare API base URL.
        """
        self._api_key = api_key
        self._base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS)
            )
        return self._session

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def fetch_klines(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 500,
    ) -> list[dict]:
        """Fetch OHLCV klines from CryptoCompare.

        Args:
            symbol: Trading symbol in format 'BTC/USDT'.
            timeframe: Timeframe: '1m', '5m', '15m', '1h', '4h', '1d'.
            limit: Maximum number of candles to return.

        Returns:
            List of candle dicts: [{time, open, high, low, close, volume}, ...]
        """
        # Convert symbol: 'BTC/USDT' -> base currency (e.g., 'BTC')
        sym = symbol.split("/")[0]

        # Map timeframe to CryptoCompare format
        timeframe_map = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "1h": "60",
            "4h": "240",
            "1d": "D",
        }
        interval = timeframe_map.get(timeframe, "60")

        # Choose appropriate endpoint based on timeframe
        if timeframe == "1d":
            url = f"{self._base_url}/data/v2/histoday"
        elif timeframe in ("1m", "5m", "15m"):
            url = f"{self._base_url}/data/v2/histominute"
        else:
            url = f"{self._base_url}/data/v2/histohour"

        params = {
            "fsym": sym,  # e.g., 'BTC'
            "tsym": "USDT",
            "limit": limit,
        }

        # Only set aggregate for 5m and 15m (to get native candles)
        # For 1h, 4h, 1d we want native timeframe, no aggregation needed
        if timeframe in ("5m", "15m"):
            params["aggregate"] = "1"

        if self._api_key:
            params["api_key"] = self._api_key

        try:
            session = await self._get_session()
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()

            if data.get("Response") != "Success":
                raise Exception(f"CryptoCompare API error: {data.get('Message', 'Unknown')}")

            candles = []
            for k in data.get("Data", {}).get("Data", []):
                candles.append({
                    "time": k.get("time", 0),
                    "open": float(k.get("open", 0)),
                    "high": float(k.get("high", 0)),
                    "low": float(k.get("low", 0)),
                    "close": float(k.get("close", 0)),
                    "volume": float(k.get("volumefrom", 0)),
                })

            logger.info("Fetched %d candles for %s from CryptoCompare", len(candles), symbol)
            return candles

        except Exception as e:
            logger.warning("Failed to fetch klines from CryptoCompare for %s: %s", symbol, e)
            raise