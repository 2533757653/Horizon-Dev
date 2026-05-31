"""Exchange module for Horizon Trading Platform."""

from .types import (
    Balance,
    OrderBook,
    OrderBookEntry,
    OrderResult,
    Ticker,
)

__all__ = [
    "Balance",
    "OrderBook",
    "OrderBookEntry",
    "OrderResult",
    "Ticker",
]


class ExchangeError(Exception):
    """Base exception for exchange-related errors."""

    pass