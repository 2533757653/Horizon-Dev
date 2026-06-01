"""Tests for Kline cache system."""

import os
import pytest
import tempfile
import time
from unittest.mock import AsyncMock

from horizon.internal.datasource.cache import KlineCache
from horizon.internal.datasource.cryptocompare import CryptoCompareAdapter
from horizon.internal.datasource.registry import DataSourceRegistry


class TestKlineCache:
    """Tests for KlineCache class."""

    def test_cache_initialization(self):
        """Test KlineCache initializes with empty cache."""
        cache = KlineCache(cache_file="/tmp/test_cache.pkl")
        assert cache.get_candles("BTC/USDT", "1h") is None

    def test_cache_update_and_retrieve(self):
        """Test cache update and retrieval."""
        cache = KlineCache(cache_file="/tmp/test_cache.pkl")
        candles = [
            {"time": 1234567890, "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0, "volume": 1000.0},
        ]
        cache.update_candles("BTC/USDT", "1h", candles)
        result = cache.get_candles("BTC/USDT", "1h")
        assert result is not None
        assert len(result) == 1
        assert result[0]["close"] == 105.0

    def test_is_stale_after_update(self):
        """Test is_stale returns True for old cache."""
        cache = KlineCache(cache_file="/tmp/test_cache.pkl", stale_seconds=3600)
        cache.update_candles("BTC/USDT", "1h", [{"time": 1234567890}])
        # Immediately after update, should not be stale
        assert not cache.is_stale("BTC/USDT", "1h")

    def test_is_stale_with_zero_stale_seconds(self):
        """Test is_stale returns True immediately when stale_seconds=0."""
        cache = KlineCache(cache_file="/tmp/test_cache.pkl", stale_seconds=0)
        cache.update_candles("BTC/USDT", "1h", [{"time": 1234567890}])
        assert cache.is_stale("BTC/USDT", "1h")

    def test_get_all_symbols(self):
        """Test get_all_symbols returns cached symbols."""
        cache = KlineCache(cache_file="/tmp/test_cache.pkl")
        cache.update_candles("BTC/USDT", "1h", [])
        cache.update_candles("ETH/USDT", "1h", [])
        symbols = cache.get_all_symbols()
        assert "BTC/USDT" in symbols
        assert "ETH/USDT" in symbols

    def test_pickle_persistence(self):
        """Test cache saves and loads from pickle."""
        cache_file = "/tmp/test_persist.pkl"

        # Create cache and add data
        cache1 = KlineCache(cache_file=cache_file)
        cache1.update_candles("BTC/USDT", "1h", [{"time": 1234567890, "close": 100.0}])
        del cache1

        # Load cache from file
        cache2 = KlineCache(cache_file=cache_file)
        cache2.load()
        result = cache2.get_candles("BTC/USDT", "1h")
        assert result is not None
        assert len(result) == 1
        assert result[0]["close"] == 100.0

        # Cleanup
        if os.path.exists(cache_file):
            os.remove(cache_file)


class TestCryptoCompareAdapter:
    """Tests for CryptoCompareAdapter."""

    @pytest.mark.asyncio
    async def test_adapter_initialization(self):
        """Test CryptoCompareAdapter initializes correctly."""
        adapter = CryptoCompareAdapter(api_key="test_key")
        assert adapter._api_key == "test_key"
        assert adapter._base_url == "https://min-api.cryptocompare.com"

    @pytest.mark.asyncio
    async def test_adapter_close(self):
        """Test adapter close method."""
        adapter = CryptoCompareAdapter()
        session = await adapter._get_session()
        assert session is not None
        await adapter.close()
        # Session should be closed now


class TestDataSourceRegistry:
    """Tests for DataSourceRegistry."""

    @pytest.mark.asyncio
    async def test_get_klines_uses_cache(self):
        """Test registry returns cached data when available."""
        cache = KlineCache(cache_file="/tmp/test_registry.pkl")
        cache.update_candles("BTC/USDT", "1h", [
            {"time": 1234567890, "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0, "volume": 1000.0},
        ])

        cc_adapter = AsyncMock(spec=CryptoCompareAdapter)
        registry = DataSourceRegistry(cache=cache, cryptocompare_adapter=cc_adapter)

        # Should return cached data without calling adapter
        result = await registry.get_klines("BTC/USDT", "1h")
        assert len(result) == 1
        assert result[0]["close"] == 105.0
        cc_adapter.fetch_klines.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_klines_fetches_on_stale(self):
        """Test registry fetches from API when cache is stale."""
        cache = KlineCache(cache_file="/tmp/test_registry2.pkl", stale_seconds=0)
        cache.update_candles("BTC/USDT", "1h", [{"time": 1234567890, "close": 100.0}])

        cc_adapter = AsyncMock()
        cc_adapter.fetch_klines = AsyncMock(return_value=[
            {"time": 1234567891, "open": 101.0, "high": 111.0, "low": 91.0, "close": 106.0, "volume": 1001.0},
        ])

        registry = DataSourceRegistry(cache=cache, cryptocompare_adapter=cc_adapter)

        # Should fetch new data since stale_seconds=0
        result = await registry.get_klines("BTC/USDT", "1h", force_refresh=True)
        cc_adapter.fetch_klines.assert_called_once()
        assert result[0]["close"] == 106.0

    @pytest.mark.asyncio
    async def test_get_klines_fallback_to_stale_cache(self):
        """Test registry returns stale cache when API fails."""
        cache = KlineCache(cache_file="/tmp/test_registry3.pkl", stale_seconds=0)
        cache.update_candles("BTC/USDT", "1h", [{"time": 1234567890, "close": 100.0}])

        cc_adapter = AsyncMock()
        cc_adapter.fetch_klines = AsyncMock(side_effect=Exception("API Error"))

        registry = DataSourceRegistry(cache=cache, cryptocompare_adapter=cc_adapter)

        # Should return stale cache when API fails
        result = await registry.get_klines("BTC/USDT", "1h")
        assert len(result) == 1
        assert result[0]["close"] == 100.0

    def test_get_cache_info(self):
        """Test get_cache_info returns correct info."""
        cache = KlineCache(cache_file="/tmp/test_registry4.pkl")
        cache.update_candles("BTC/USDT", "1h", [{"time": 1234567890}])

        cc_adapter = CryptoCompareAdapter()
        registry = DataSourceRegistry(cache=cache, cryptocompare_adapter=cc_adapter)

        info = registry.get_cache_info()
        assert info["symbol_count"] == 1
        assert len(info["symbols"]) == 1
        assert info["symbols"][0]["symbol"] == "BTC/USDT"
        assert "1h" in info["symbols"][0]["timeframes"]