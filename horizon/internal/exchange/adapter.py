"""Abstract base class for exchange adapters."""

import abc
from decimal import Decimal
from typing import Optional

from .types import (
    Balance,
    OrderBook,
    OrderResult,
    Ticker,
)


class ExchangeAdapter(abc.ABC):
    """Abstract base class for exchange adapters.

    All exchange adapters must implement this interface.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Return the exchange name."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def enabled(self) -> bool:
        """Return whether the exchange is enabled."""
        raise NotImplementedError

    async def fetch_ticker(self, symbol: str) -> Ticker:
        """Fetch current ticker for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT').

        Returns:
            Ticker with current price and volume information.

        Raises:
            ExchangeError: If the request fails.
        """
        raise NotImplementedError

    async def fetch_orderbook(self, symbol: str, depth: int = 5) -> OrderBook:
        """Fetch order book for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT').
            depth: Number of price levels to return.

        Returns:
            OrderBook with bids and asks.

        Raises:
            ExchangeError: If the request fails.
        """
        raise NotImplementedError

    async def fetch_recent_trades(self, symbol: str, limit: int = 50) -> list[dict]:
        """Fetch recent trades for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT').
            limit: Maximum number of trades to return.

        Returns:
            List of trade dictionaries with keys: price, size, side, timestamp_ms.

        Raises:
            ExchangeError: If the request fails.
        """
        raise NotImplementedError

    async def fetch_balance(self, asset: str) -> Balance:
        """Fetch balance for a specific asset.

        Args:
            asset: Asset symbol (e.g., 'BTC', 'USDT').

        Returns:
            Balance for the specified asset.

        Raises:
            ExchangeError: If the request fails.
        """
        raise NotImplementedError

    async def fetch_all_balances(self) -> list[Balance]:
        """Fetch all account balances.

        Returns:
            List of all account balances.

        Raises:
            ExchangeError: If the request fails.
        """
        raise NotImplementedError

    async def place_market_order(
        self, symbol: str, side: str, volume: Decimal
    ) -> OrderResult:
        """Place a market order.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT').
            side: Order side ('buy' or 'sell').
            volume: Order volume.

        Returns:
            OrderResult with order details.

        Raises:
            ExchangeError: If the order placement fails.
        """
        raise NotImplementedError

    async def place_limit_order(
        self, symbol: str, side: str, price: Decimal, volume: Decimal
    ) -> OrderResult:
        """Place a limit order.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT').
            side: Order side ('buy' or 'sell').
            price: Limit price.
            volume: Order volume.

        Returns:
            OrderResult with order details.

        Raises:
            ExchangeError: If the order placement fails.
        """
        raise NotImplementedError

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> bool:
        """Cancel an open order.

        Args:
            exchange_order_id: Exchange-specific order ID.
            symbol: Trading pair symbol (e.g., 'BTC/USDT').

        Returns:
            True if cancellation was successful.

        Raises:
            ExchangeError: If the cancellation fails.
        """
        raise NotImplementedError

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> list[OrderResult]:
        """Fetch all open orders, optionally filtered by symbol.

        Args:
            symbol: Optional trading pair symbol to filter by.

        Returns:
            List of open order results.

        Raises:
            ExchangeError: If the request fails.
        """
        raise NotImplementedError

    async def fetch_order(self, exchange_order_id: str, symbol: str) -> OrderResult:
        """Fetch details of a specific order.

        Args:
            exchange_order_id: Exchange-specific order ID.
            symbol: Trading pair symbol (e.g., 'BTC/USDT').

        Returns:
            OrderResult with order details.

        Raises:
            ExchangeError: If the request fails.
        """
        raise NotImplementedError