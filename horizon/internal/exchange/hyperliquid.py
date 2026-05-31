"""Hyperliquid exchange adapter."""

import logging
import time
from decimal import Decimal
from typing import Optional

import aiohttp

from .adapter import ExchangeAdapter
from .types import (
    Balance,
    OrderBook,
    OrderBookEntry,
    OrderResult,
    Ticker,
)

from . import ExchangeError

logger = logging.getLogger(__name__)


class HyperliquidAdapter(ExchangeAdapter):
    """Hyperliquid exchange adapter.

    Implements the ExchangeAdapter interface for the Hyperliquid exchange.
    This adapter uses the Hyperliquid Info API for read operations.
    Write operations (order placement, cancellation) are stubs that log warnings
    and return safe fallback values, as full EIP-712 signing is deferred.
    """

    BASE_URL = "https://api.hyperliquid.xyz"
    TIMEOUT_SECONDS = 10

    def __init__(self, wallet_address: str, private_key: str):
        """Initialize the Hyperliquid adapter.

        Args:
            wallet_address: Hyperliquid wallet address.
            private_key: Hyperliquid private key for signing.
        """
        self._wallet_address = wallet_address
        self._private_key = private_key
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def name(self) -> str:
        """Return the exchange name."""
        return "hyperliquid"

    @property
    def enabled(self) -> bool:
        """Return whether the exchange is enabled."""
        return self._wallet_address != "" and self._private_key != ""

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.TIMEOUT_SECONDS)
            )
        return self._session

    async def _close_session(self) -> None:
        """Close the aiohttp session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _request(self, payload: dict) -> dict:
        """Make a POST request to the Hyperliquid Info API.

        Args:
            payload: JSON payload for the request.

        Returns:
            JSON response as dictionary.

        Raises:
            ExchangeError: If the request fails.
        """
        url = f"{self.BASE_URL}/info"
        headers = {"Content-Type": "application/json"}

        try:
            session = await self._get_session()
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status >= 400:
                    error_text = await response.text()
                    raise ExchangeError(
                        f"Hyperliquid HTTP {response.status}: {error_text}"
                    )

                return await response.json()

        except aiohttp.ClientError as e:
            raise ExchangeError(str(e)) from e

    async def fetch_ticker(self, symbol: str) -> Ticker:
        """Fetch current ticker for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT' or 'BTC/USDT').

        Returns:
            Ticker with current price and volume information.

        Raises:
            ExchangeError: If the request fails.
        """
        payload = {"type": "metaAndAssetCtxs"}
        response = await self._request(payload)

        # Hyperliquid API returns {"response": {...}} format
        data = response.get("response", {}) if isinstance(response, dict) else response

        # Parse meta and asset contexts to find the requested symbol
        # The data is a list: [meta, assetCtxs]
        if not isinstance(data, list) or len(data) < 2:
            raise ExchangeError(f"Hyperliquid: unexpected response format for {symbol}")

        asset_ctxs = data[1] if len(data) > 1 else []

        # Find the coin name from the symbol (strip / from symbol)
        coin = symbol.replace("/", "").replace("USDT", "").replace("USDC", "")

        # Find matching asset context
        ticker_data = None
        for ctx in asset_ctxs:
            if isinstance(ctx, dict) and ctx.get("coin") == coin:
                ticker_data = ctx
                break

        if ticker_data is None:
            raise ExchangeError(f"Hyperliquid: no ticker data found for {symbol}")

        # Extract mark price and 24h volume
        mark_price = Decimal(str(ticker_data.get("markPx", "0")))
        day_volume = Decimal(str(ticker_data.get("dayNtlVlm", "0")))

        return Ticker(
            symbol=symbol,
            price=mark_price,
            volume_24h=day_volume,
            exchange=self.name,
            timestamp_ms=int(time.time() * 1000),
        )

    async def fetch_orderbook(self, symbol: str, depth: int = 5) -> OrderBook:
        """Fetch order book for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT' or 'BTC/USDT').
            depth: Number of price levels to return.

        Returns:
            OrderBook with bids and asks.

        Raises:
            ExchangeError: If the request fails.
        """
        coin = symbol.replace("/", "").replace("USDT", "").replace("USDC", "")
        payload = {"type": "l2Book", "coin": coin}
        response = await self._request(payload)

        # Hyperliquid API returns {"response": {...}} format
        data = response.get("response", {}) if isinstance(response, dict) else response

        if not isinstance(data, dict):
            raise ExchangeError(f"Hyperliquid: invalid orderbook response for {symbol}")

        levels = data.get("levels", [])
        if len(levels) < 2:
            raise ExchangeError(f"Hyperliquid: invalid orderbook response for {symbol}")

        # levels[0] = bids, levels[1] = asks
        bids_raw = levels[0].get("levels", [])
        asks_raw = levels[1].get("levels", [])

        bids = []
        for entry in bids_raw[:depth]:
            # Each level is [px, sz, n] where px=price, sz=size, n=number of orders
            px = Decimal(str(entry[0]))
            sz = Decimal(str(entry[1]))
            bids.append(OrderBookEntry(price=px, size=sz))

        asks = []
        for entry in asks_raw[:depth]:
            px = Decimal(str(entry[0]))
            sz = Decimal(str(entry[1]))
            asks.append(OrderBookEntry(price=px, size=sz))

        return OrderBook(
            symbol=symbol,
            exchange=self.name,
            bids=bids,
            asks=asks,
        )

    async def fetch_balance(self, asset: str) -> Balance:
        """Fetch balance for a specific asset.

        Args:
            asset: Asset symbol (e.g., 'BTC', 'USDT').

        Returns:
            Balance for the specified asset.

        Raises:
            ExchangeError: If the request fails.
        """
        balances = await self.fetch_all_balances()
        for balance in balances:
            if balance.asset == asset:
                return balance

        return Balance(
            asset=asset,
            free=Decimal("0"),
            locked=Decimal("0"),
            exchange=self.name,
        )

    async def fetch_all_balances(self) -> list[Balance]:
        """Fetch all account balances.

        Returns:
            List of all account balances.

        Raises:
            ExchangeError: If the request fails.
        """
        if not self._wallet_address:
            raise ExchangeError(
                "Hyperliquid: wallet address not configured for balance fetch"
            )

        payload = {"type": "spotClearinghouseState", "user": self._wallet_address}
        data = await self._request(payload)

        balances = []
        spot_balances = data.get("spotBalances", [])

        for balance_data in spot_balances:
            coin = balance_data.get("coin", "")
            # Hyperliquid spot balances have total and locked
            # We treat total as free since detailed breakdown isn't provided
            total = Decimal(str(balance_data.get("total", "0")))
            locked = Decimal(str(balance_data.get("locked", "0")))
            free = total - locked

            if total > 0 or locked > 0:
                balances.append(
                    Balance(
                        asset=coin,
                        free=free,
                        locked=locked,
                        exchange=self.name,
                    )
                )

        return balances

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
        coin = symbol.replace("/", "")
        payload = {"type": "tradeHistory", "coin": coin}
        data = await self._request(payload)

        trades = []
        for trade_data in data[:limit]:
            # Trade history entries are [time, price, size, side, numLevel]
            timestamp_ms = int(trade_data[0]) if trade_data[0] else 0
            price = str(trade_data[1]) if len(trade_data) > 1 else "0"
            size = str(trade_data[2]) if len(trade_data) > 2 else "0"
            side = str(trade_data[3]).lower() if len(trade_data) > 3 else "unknown"

            trades.append({
                "price": price,
                "size": size,
                "side": side,
                "timestamp_ms": timestamp_ms,
            })

        return trades

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> list[OrderResult]:
        """Fetch all open orders, optionally filtered by symbol.

        Args:
            symbol: Optional trading pair symbol to filter by.

        Returns:
            List of open order results.

        Raises:
            ExchangeError: If the request fails.
        """
        if not self._wallet_address:
            raise ExchangeError(
                "Hyperliquid: wallet address not configured for open orders fetch"
            )

        payload = {"type": "openOrders", "user": self._wallet_address}
        data = await self._request(payload)

        orders = []
        for order_data in data:
            # Open orders have: coin, side, sz, price, orderId, filled, ts
            coin = order_data.get("coin", "")
            order_symbol = f"{coin}/USDT" if coin else symbol or ""

            # Filter by symbol if specified
            if symbol and order_symbol.replace("/", "") != symbol.replace("/", ""):
                continue

            order_result = OrderResult(
                order_id=str(order_data.get("orderId", "")),
                exchange_order_id=str(order_data.get("orderId", "")),
                exchange=self.name,
                symbol=order_symbol,
                side=str(order_data.get("side", "")).lower(),
                order_type="limit",
                price=Decimal(str(order_data.get("price", "0"))),
                volume=Decimal(str(order_data.get("sz", "0"))),
                filled_volume=Decimal(str(order_data.get("filled", "0"))),
                status="submitted",
                created_at_ms=int(order_data.get("ts", 0)),
                updated_at_ms=int(order_data.get("ts", 0)),
            )
            orders.append(order_result)

        return orders

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
        logger.warning(
            "Hyperliquid fetch_order not fully implemented — "
            "returning synthetic order result for order_id=%s",
            exchange_order_id,
        )

        # Return a synthetic order result since Hyperliquid Info API
        # does not provide a direct order lookup endpoint
        return OrderResult(
            order_id=exchange_order_id,
            exchange_order_id=exchange_order_id,
            exchange=self.name,
            symbol=symbol,
            side="unknown",
            order_type="limit",
            price=Decimal("0"),
            volume=Decimal("0"),
            filled_volume=Decimal("0"),
            status="unknown",
            created_at_ms=0,
            updated_at_ms=0,
        )

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
        logger.warning(
            "Hyperliquid order signing not fully implemented — order not submitted. "
            "symbol=%s, side=%s, volume=%s",
            symbol,
            side,
            volume,
        )

        return OrderResult(
            order_id="",
            exchange_order_id="",
            exchange=self.name,
            symbol=symbol,
            side=side,
            order_type="market",
            price=None,
            volume=volume,
            filled_volume=Decimal("0"),
            status="rejected",
            created_at_ms=int(time.time() * 1000),
            updated_at_ms=int(time.time() * 1000),
        )

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
        logger.warning(
            "Hyperliquid order signing not fully implemented — order not submitted. "
            "symbol=%s, side=%s, price=%s, volume=%s",
            symbol,
            side,
            price,
            volume,
        )

        return OrderResult(
            order_id="",
            exchange_order_id="",
            exchange=self.name,
            symbol=symbol,
            side=side,
            order_type="limit",
            price=price,
            volume=volume,
            filled_volume=Decimal("0"),
            status="rejected",
            created_at_ms=int(time.time() * 1000),
            updated_at_ms=int(time.time() * 1000),
        )

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
        logger.warning(
            "Hyperliquid order cancellation not fully implemented — "
            "order not cancelled. exchange_order_id=%s, symbol=%s",
            exchange_order_id,
            symbol,
        )
        return False

    async def close(self) -> None:
        """Close the adapter and release resources."""
        await self._close_session()