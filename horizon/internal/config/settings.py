"""
Configuration system for Horizon Trading Platform.
Uses Pydantic Settings with YAML and environment variable support.
"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from typing import Optional, Dict, Any
import json
import logging
from pathlib import Path
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


class DatabaseSettings(BaseSettings):
    """Database configuration."""
    path: str = "./data/horizon.db"


class LLMSettings(BaseSettings):
    """LLM configuration."""
    enabled: bool = False
    api_key: str = ""
    model: str = "claude-3-5-sonnet-20241022"
    analysis_interval_hours: int = 8


class MarketDataSettings(BaseSettings):
    """Market data configuration."""


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


def load_keys_from_file(path: str) -> Dict[str, Any]:
    """Load API keys from a JSON file."""
    try:
        file_path = Path(path)
        if not file_path.exists():
            logger.warning(f"Keys file not found: {path}")
            return {}
        content = file_path.read_text(encoding="utf-8")
        keys = json.loads(content)
        if not isinstance(keys, dict):
            logger.warning(f"Keys file does not contain a valid object: {path}")
            return {}
        return keys
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in keys file {path}: {e}")
        return {}
    except Exception as e:
        logger.warning(f"Failed to load keys from {path}: {e}")
        return {}