"""CryptoCompare data adapter stub."""

from typing import Optional


class CryptoCompareAdapter:
    """CryptoCompare adapter stub."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key

    def fetch_klines(self, symbol: str, timeframe: str, limit: int = 100):
        """Stub method."""
        return []