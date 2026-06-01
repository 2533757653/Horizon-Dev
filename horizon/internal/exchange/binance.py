"""Binance exchange adapter."""

import asyncio
import hashlib
import hmac
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
    SymbolInfo,
    Ticker,
)

from . import ExchangeError


class BinanceAdapter(ExchangeAdapter):
    """Binance exchange adapter.

    Implements the ExchangeAdapter interface for the Binance exchange.
    """

    BASE_URL = "https://api.binance.com"
    TIMEOUT_SECONDS = 10

    def __init__(
        self, api_key: str, api_secret: str, recv_window_ms: int = 5000
    ):
        """Initialize the Binance adapter.

        Args:
            api_key: Binance API key.
            api_secret: Binance API secret.
            recv_window_ms: Receive window in milliseconds for signed requests.
        """
        self._api_key = api_key
        self._api_secret = api_secret
        self._recv_window_ms = recv_window_ms
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def name(self) -> str:
        """Return the exchange name."""
        return "binance"

    @property
    def enabled(self) -> bool:
        """Return whether the exchange is enabled."""
        return self._api_key != "" and self._api_secret != ""

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

    def _sign_request(self, query_string: str) -> str:
        """Generate HMAC-SHA256 signature for a query string.

        Args:
            query_string: The query string to sign.

        Returns:
            The hexadecimal HMAC-SHA256 signature.
        """
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def _prepare_auth_params(self) -> dict:
        """Prepare common authentication parameters.

        Returns:
            Dictionary with timestamp, recvWindow, and signature.
        """
        timestamp_ms = int(time.time() * 1000)
        params = f"timestamp={timestamp_ms}&recvWindow={self._recv_window_ms}"
        signature = self._sign_request(params)
        return {
            "timestamp": str(timestamp_ms),
            "recvWindow": str(self._recv_window_ms),
            "signature": signature,
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        authenticated: bool = False,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> dict:
        """Make an HTTP request to the Binance API.

        Args:
            method: HTTP method (GET, POST, DELETE).
            endpoint: API endpoint path.
            authenticated: Whether the request requires authentication.
            params: Query parameters.
            data: Form data for POST requests.

        Returns:
            JSON response as dictionary.

        Raises:
            ExchangeError: If the request fails.
        """
        url = f"{self.BASE_URL}{endpoint}"
        headers = {}

        if authenticated:
            headers["X-MBX-APIKEY"] = self._api_key
            all_params = dict(params) if params else {}
            all_params.update(self._prepare_auth_params())
            params = all_params

        try:
            session = await self._get_session()
            async with session.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers if headers else None,
            ) as response:
                if response.status >= 400:
                    error_text = await response.text()
                    try:
                        error_data = await response.json()
                        code = error_data.get("code", response.status)
                        msg = error_data.get("msg", error_text)
                        raise ExchangeError(f"Binance {code}: {msg}")
                    except Exception:
                        raise ExchangeError(
                            f"Binance HTTP {response.status}: {error_text}"
                        )

                return await response.json()

        except aiohttp.ClientError as e:
            raise ExchangeError(str(e)) from e

    def _convert_symbol(self, symbol: str) -> str:
        """Convert generic symbol format to Binance format.

        Args:
            symbol: Symbol in generic format (e.g., 'BTC/USDT').

        Returns:
            Binance symbol format (e.g., 'BTCUSDT').
        """
        return symbol.replace("/", "")

    async def fetch_ticker(self, symbol: str) -> Ticker:
        """Fetch current ticker for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT').

        Returns:
            Ticker with current price and volume information.

        Raises:
            ExchangeError: If the request fails.
        """
        binance_symbol = self._convert_symbol(symbol)
        params = {"symbol": binance_symbol}

        data = await self._request("GET", "/api/v3/ticker/24hr", params=params)

        return Ticker(
            symbol=symbol,
            price=Decimal(data["lastPrice"]),
            volume_24h=Decimal(data["quoteVolume"]),
            exchange=self.name,
            timestamp_ms=int(time.time() * 1000),
        )

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
        binance_symbol = self._convert_symbol(symbol)
        params = {"symbol": binance_symbol, "limit": depth}

        data = await self._request("GET", "/api/v3/depth", params=params)

        bids = [
            OrderBookEntry(price=Decimal(entry[0]), size=Decimal(entry[1]))
            for entry in data.get("bids", [])
        ]
        asks = [
            OrderBookEntry(price=Decimal(entry[0]), size=Decimal(entry[1]))
            for entry in data.get("asks", [])
        ]

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
        data = await self._request(
            "GET", "/api/v3/account", authenticated=True
        )

        balances = []
        for balance_data in data.get("balances", []):
            free = Decimal(balance_data["free"])
            locked = Decimal(balance_data["locked"])
            if free > 0 or locked > 0:
                balances.append(
                    Balance(
                        asset=balance_data["asset"],
                        free=free,
                        locked=locked,
                        exchange=self.name,
                    )
                )

        return balances

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
        binance_symbol = self._convert_symbol(symbol)
        params = {
            "symbol": binance_symbol,
            "side": side.upper(),
            "type": "MARKET",
            "quantity": str(volume),
        }

        data = await self._request(
            "POST", "/api/v3/order", authenticated=True, params=params
        )

        return self._parse_order_result(data, symbol)

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
        binance_symbol = self._convert_symbol(symbol)
        params = {
            "symbol": binance_symbol,
            "side": side.upper(),
            "type": "LIMIT",
            "timeInForce": "GTC",
            "price": str(price),
            "quantity": str(volume),
        }

        data = await self._request(
            "POST", "/api/v3/order", authenticated=True, params=params
        )

        return self._parse_order_result(data, symbol)

    async def cancel_order(
        self, exchange_order_id: str, symbol: str
    ) -> bool:
        """Cancel an open order.

        Args:
            exchange_order_id: Exchange-specific order ID.
            symbol: Trading pair symbol (e.g., 'BTC/USDT').

        Returns:
            True if cancellation was successful.

        Raises:
            ExchangeError: If the cancellation fails.
        """
        binance_symbol = self._convert_symbol(symbol)
        params = {
            "symbol": binance_symbol,
            "orderId": exchange_order_id,
        }

        await self._request(
            "DELETE", "/api/v3/order", authenticated=True, params=params
        )

        return True

    async def fetch_open_orders(
        self, symbol: Optional[str] = None
    ) -> list[OrderResult]:
        """Fetch all open orders, optionally filtered by symbol.

        Args:
            symbol: Optional trading pair symbol to filter by.

        Returns:
            List of open order results.

        Raises:
            ExchangeError: If the request fails.
        """
        params = {}
        if symbol:
            params["symbol"] = self._convert_symbol(symbol)

        data = await self._request(
            "GET", "/api/v3/openOrders", authenticated=True, params=params
        )

        return [
            self._parse_order_result(order_data, symbol or order_data["symbol"])
            for order_data in data
        ]

    async def fetch_order(
        self, exchange_order_id: str, symbol: str
    ) -> OrderResult:
        """Fetch details of a specific order.

        Args:
            exchange_order_id: Exchange-specific order ID.
            symbol: Trading pair symbol (e.g., 'BTC/USDT').

        Returns:
            OrderResult with order details.

        Raises:
            ExchangeError: If the request fails.
        """
        binance_symbol = self._convert_symbol(symbol)
        params = {
            "symbol": binance_symbol,
            "orderId": exchange_order_id,
        }

        data = await self._request(
            "GET", "/api/v3/order", authenticated=True, params=params
        )

        return self._parse_order_result(data, symbol)

    def _parse_order_result(self, data: dict, symbol: str) -> OrderResult:
        """Parse a Binance order response into an OrderResult.

        Args:
            data: Raw Binance order response.
            symbol: Trading pair symbol in generic format.

        Returns:
            OrderResult parsed from the response.
        """
        status_mapping = {
            "NEW": "submitted",
            "PARTIALLY_FILLED": "partially_filled",
            "FILLED": "filled",
            "CANCELED": "cancelled",
            "REJECTED": "rejected",
            "PENDING_NEW": "pending",
            "TRADING": "submitted",
        }

        return OrderResult(
            order_id=str(data["orderId"]),
            exchange_order_id=str(data["orderId"]),
            exchange=self.name,
            symbol=symbol,
            side=data["side"].lower(),
            order_type=data["type"].lower(),
            price=Decimal(data["price"]) if "price" in data else None,
            volume=Decimal(data["origQty"]),
            filled_volume=Decimal(data["executedQty"]),
            status=status_mapping.get(data["status"], "unknown"),
            created_at_ms=data.get("transactTime", 0),
            updated_at_ms=data.get("updateTime", data.get("transactTime", 0)),
        )

    async def fetch_all_symbols(self) -> list[SymbolInfo]:
        """Fetch all tradeable symbols from Binance.

        Returns:
            List of SymbolInfo for all symbols with status=TRADING.

        Raises:
            ExchangeError: If the request fails.
        """
        data = await self._request("GET", "/api/v3/exchangeInfo")

        symbols = []
        for sym in data.get("symbols", []):
            if sym.get("status") != "TRADING":
                continue

            base = sym["baseAsset"]
            quote = sym["quoteAsset"]

            # Convert Binance's BTCUSDT to generic BTC/USDT
            generic_symbol = f"{base}/{quote}"

            symbols.append(
                SymbolInfo(
                    exchange=self.name,
                    symbol=generic_symbol,
                    base_asset=base,
                    quote_asset=quote,
                    volume_24h=None,
                    price=None,
                )
            )

        return symbols

    async def close(self) -> None:
        """Close the adapter and release resources."""
        await self._close_session()