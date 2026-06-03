"""
Configuration system for Horizon Trading Platform.
Uses Pydantic Settings with YAML and environment variable support.
"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from typing import Optional, Dict, Any
import logging
import yaml

logger = logging.getLogger(__name__)


class ExchangeConfig(BaseSettings):
    """Base configuration for exchange connections."""
    enabled: bool = True
    recv_window_ms: int = 5000
    api_key: str = ""
    api_secret: str = ""


class HyperliquidConfig(ExchangeConfig):
    """Hyperliquid-specific configuration."""
    wallet_address: str = ""
    private_key: str = ""


class BitgetConfig(ExchangeConfig):
    """Bitget-specific configuration."""
    passphrase: str = ""


class BinanceConfig(ExchangeConfig):
    """Binance-specific configuration."""
    pass


class HTXConfig(ExchangeConfig):
    """HTX-specific configuration."""
    pass


class ExchangesSettings(BaseSettings):
    """Container for all exchange configurations."""
    binance: BinanceConfig = Field(default_factory=BinanceConfig)
    htx: HTXConfig = Field(default_factory=HTXConfig)
    hyperliquid: HyperliquidConfig = Field(default_factory=HyperliquidConfig)
    bitget: BitgetConfig = Field(default_factory=BitgetConfig)
    active_exchange: str = Field(default="hyperliquid", description="The active exchange for all operations")


class AppSettings(BaseSettings):
    """Application-level settings."""
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "info"


class TradingSettings(BaseSettings):
    """Trading-specific settings."""
    market_data_poll_interval_seconds: int = 10
    portfolio_snapshot_interval_seconds: int = 60
    order_sync_interval_seconds: int = 30
    initial_cash_usdt: float = 20000.0  # Paper trading starting cash


class DatabaseSettings(BaseSettings):
    """Database configuration."""
    db_path: str = "./data/horizon.db"


class LLMSettings(BaseSettings):
    """LLM configuration."""
    enabled: bool = False
    api_key: str = ""
    base_url: str = ""
    model: str = "claude-3-5-sonnet-20241022"
    analysis_interval_hours: int = 8


class DataSourceSettings(BaseSettings):
    """Data source configuration for kline cache."""
    cryptocompare_api_key: str = ""
    cryptocompare_base_url: str = "https://min-api.cryptocompare.com"
    cache_stale_seconds: int = 3600  # 1 hour
    cache_file: str = "./data/kline_cache.pkl"


class MarketDataSettings(BaseSettings):
    """Market data configuration."""
    symbols: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]


def _load_yaml_config() -> Dict[str, Any]:
    """Load configuration from YAML file."""
    yaml_path = os.path.join(os.path.dirname(__file__), "..", "..", "config.yaml")
    try:
        with open(yaml_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Failed to load config.yaml: {e}")
        return {}


class Settings(BaseSettings):
    """Root settings class."""
    app: AppSettings = Field(default_factory=AppSettings)
    exchanges: ExchangesSettings = Field(default_factory=ExchangesSettings)
    trading: TradingSettings = Field(default_factory=TradingSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    market_data: MarketDataSettings = Field(default_factory=MarketDataSettings)
    datasource: DataSourceSettings = Field(default_factory=DataSourceSettings)

    @classmethod
    def from_yaml(cls, yaml_path: str = "config.yaml") -> "Settings":
        """Create Settings from YAML file."""
        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f) or {}
            return cls(**config_data)
        except Exception as e:
            logger.warning(f"Failed to load config: {e}")
            return cls()

    @field_validator("app", "exchanges", "trading", "database", "llm", "market_data", mode="before")
    @classmethod
    def validate_sections(cls, v):
        return v if v else {}


# Try to load YAML config at module level
_yaml_config = _load_yaml_config()

def _create_settings_from_yaml() -> Settings:
    """Create Settings instance from loaded YAML config."""
    try:
        return Settings(**_yaml_config)
    except Exception:
        return Settings()

settings = _create_settings_from_yaml()