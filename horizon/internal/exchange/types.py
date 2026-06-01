"""Type definitions for exchange adapters."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class Ticker:
    """Ticker information for a trading pair."""

    symbol: str
    price: Decimal
    volume_24h: Decimal
    exchange: str
    timestamp_ms: int


@dataclass(frozen=True)
class OrderBookEntry:
    """A single entry in an order book."""

    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class OrderBook:
    """Order book with bids and asks."""

    symbol: str
    exchange: str
    bids: list[OrderBookEntry]
    asks: list[OrderBookEntry]


@dataclass(frozen=True)
class Balance:
    """Account balance for an asset."""

    asset: str
    free: Decimal
    locked: Decimal
    exchange: str


@dataclass(frozen=True)
class OrderResult:
    """Result of an order operation."""

    order_id: str
    exchange_order_id: str
    exchange: str
    symbol: str
    side: str
    order_type: str
    price: Optional[Decimal]
    volume: Decimal
    filled_volume: Decimal
    status: str
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True)
class SymbolInfo:
    """Symbol information from an exchange's symbol listing API."""

    exchange: str
    symbol: str          # generic format, e.g. "BTC/USDT"
    base_asset: str      # e.g. "BTC"
    quote_asset: str     # e.g. "USDT"
    volume_24h: Optional[Decimal]
    price: Optional[Decimal]