"""Registry for exchange adapters."""

import asyncio
from typing import Optional

from .adapter import ExchangeAdapter
from .types import Ticker


class ExchangeRegistry:
    """Registry for managing exchange adapters.

    Allows registration and retrieval of exchange adapters by name.
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._adapters: dict[str, ExchangeAdapter] = {}

    def register(self, adapter: ExchangeAdapter) -> None:
        """Register an exchange adapter.

        Args:
            adapter: The exchange adapter to register.
        """
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> Optional[ExchangeAdapter]:
        """Get an exchange adapter by name.

        Args:
            name: The exchange name.

        Returns:
            The exchange adapter if found, None otherwise.
        """
        return self._adapters.get(name)

    def list_all(self) -> list[ExchangeAdapter]:
        """List all registered exchange adapters.

        Returns:
            List of all registered adapters.
        """
        return list(self._adapters.values())

    def list_enabled(self) -> list[ExchangeAdapter]:
        """List all enabled exchange adapters.

        Returns:
            List of enabled adapters.
        """
        return [adapter for adapter in self._adapters.values() if adapter.enabled]

    async def get_all_tickers(self, symbol: str) -> list[Ticker]:
        """Fetch tickers for a symbol from all enabled exchanges.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT').

        Returns:
            List of tickers from all enabled exchanges (errors are caught per exchange).
        """
        enabled = self.list_enabled()
        results = await asyncio.gather(
            *[
                adapter.fetch_ticker(symbol)
                for adapter in enabled
            ],
            return_exceptions=True,
        )
        return [result for result in results if isinstance(result, Ticker)]