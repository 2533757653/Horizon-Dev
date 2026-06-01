"""Bitget exchange adapter."""

import asyncio
import base64
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


class BitgetAdapter(ExchangeAdapter):
    """Bitget exchange adapter.

    Implements the ExchangeAdapter interface for the Bitget exchange.
    """

    BASE_URL = "https://api.bitget.com"
    TIMEOUT_SECONDS = 10

    def __init__(self, api_key: str, api_secret: str, passphrase: str):
        """Initialize the Bitget adapter.

        Args:
            api_key: Bitget API key.
            api_secret: Bitget API secret.
            passphrase: Bitget API passphrase.
        """
        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def name(self) -> str:
        """Return the exchange name."""
        return "bitget"

    @property
    def enabled(self) -> bool:
        """Return whether the exchange is enabled."""
        return self._api_key != "" and self._api_secret != "" and self._passphrase != ""

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

    def _sign_request(self, timestamp: str, method: str, request_path: str, body: str) -> str:
        """Generate HMAC-SHA256 signature for Bitget v2 API.

        Args:
            timestamp: UTC timestamp in milliseconds as string.
            method: HTTP method (e.g., 'GET', 'POST').
            request_path: API endpoint path including query string.
            body: Request body string (empty string for GET requests).

        Returns:
            Base64-encoded HMAC-SHA256 signature.
        """
        message = timestamp + method + request_path + body
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(signature.digest()).decode("utf-8")

    def _prepare_auth_headers(self, method: str, request_path: str, body: str = "") -> dict:
        """Prepare authentication headers for Bitget v2 API.

        Args:
            method: HTTP method (e.g., 'GET', 'POST').
            request_path: API endpoint path including query string.
            body: Request body string (empty string for GET requests).

        Returns:
            Dictionary with all required authentication headers.
        """
        timestamp = str(int(time.time() * 1000))
        signature = self._sign_request(timestamp, method, request_path, body)

        return {
            "ACCESS-KEY": self._api_key,
            "ACCESS-PASSPHRASE": self._passphrase,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-SIGN": signature,
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        authenticated: bool = False,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> dict:
        """Make an HTTP request to the Bitget API.

        Args:
            method: HTTP method (GET, POST, DELETE).
            endpoint: API endpoint path.
            authenticated: Whether the request requires authentication.
            params: Query parameters.
            data: JSON body data for POST requests.

        Returns:
            JSON response as dictionary.

        Raises:
            ExchangeError: If the request fails.
        """
        url = f"{self.BASE_URL}{endpoint}"
        headers = {}

        if authenticated:
            body = "" if data is None else self._json_dumps(data)
            headers = self._prepare_auth_headers(method, endpoint, body)

        try:
            session = await self._get_session()
            async with session.request(
                method,
                url,
                params=params,
                json=data if data else None,
                headers=headers if headers else None,
            ) as response:
                if response.status >= 400:
                    error_text = await response.text()
                    try:
                        error_data = await response.json()
                        code = error_data.get("code", response.status)
                        msg = error_data.get("msg", error_text)
                        raise ExchangeError(f"Bitget {code}: {msg}")
                    except Exception:
                        raise ExchangeError(
                            f"Bitget HTTP {response.status}: {error_text}"
                        )

                return await response.json()

        except aiohttp.ClientError as e:
            raise ExchangeError(str(e)) from e

    def _json_dumps(self, data: dict) -> str:
        """Serialize data to JSON string for signing.

        Args:
            data: Dictionary to serialize.

        Returns:
            JSON string representation.
        """
        import json
        return json.dumps(data, separators=(",", ":"))

    def _convert_symbol(self, symbol: str) -> str:
        """Convert generic symbol format to Bitget format.

        Args:
            symbol: Symbol in generic format (e.g., 'BTC/USDT').

        Returns:
            Bitget symbol format (e.g., 'BTCUSDT').
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
        bitget_symbol = self._convert_symbol(symbol)
        params = {"symbol": bitget_symbol}

        response = await self._request(
            "GET", "/api/v2/spot/market/ticker", params=params
        )

        data = response.get("data", {})
        return Ticker(
            symbol=symbol,
            price=Decimal(data.get("lastPr", "0")),
            volume_24h=Decimal(data.get("vol24h", "0")),
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
        bitget_symbol = self._convert_symbol(symbol)
        params = {"symbol": bitget_symbol, "limit": str(depth)}

        response = await self._request(
            "GET", "/api/v2/spot/market/books", params=params
        )

        data = response.get("data", {})
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
        response = await self._request(
            "GET", "/api/v2/spot/account/assets", authenticated=True
        )

        balances = []
        for asset_data in response.get("data", []):
            coin_name = asset_data.get("coinName", "")
            available = Decimal(asset_data.get("available", "0"))
            frozen = Decimal(asset_data.get("frozen", "0"))
            if available > 0 or frozen > 0:
                balances.append(
                    Balance(
                        asset=coin_name,
                        free=available,
                        locked=frozen,
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
        bitget_symbol = self._convert_symbol(symbol)
        order_data = {
            "symbol": bitget_symbol,
            "side": side.lower(),
            "orderType": "market",
            "size": str(volume),
        }

        response = await self._request(
            "POST", "/api/v2/spot/trade/order", authenticated=True, data=order_data
        )

        return self._parse_order_result(response.get("data", {}), symbol)

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
        bitget_symbol = self._convert_symbol(symbol)
        order_data = {
            "symbol": bitget_symbol,
            "side": side.lower(),
            "orderType": "limit",
            "price": str(price),
            "size": str(volume),
        }

        response = await self._request(
            "POST", "/api/v2/spot/trade/order", authenticated=True, data=order_data
        )

        return self._parse_order_result(response.get("data", {}), symbol)

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
        bitget_symbol = self._convert_symbol(symbol)
        cancel_data = {
            "symbol": bitget_symbol,
            "orderId": exchange_order_id,
        }

        await self._request(
            "POST", "/api/v2/spot/trade/cancel-order", authenticated=True, data=cancel_data
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

        response = await self._request(
            "GET", "/api/v2/spot/trade/unfilled-orders", authenticated=True, params=params
        )

        return [
            self._parse_order_result(order_data, symbol or order_data.get("symbol", ""))
            for order_data in response.get("data", [])
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
        bitget_symbol = self._convert_symbol(symbol)
        params = {
            "symbol": bitget_symbol,
            "orderId": exchange_order_id,
        }

        response = await self._request(
            "GET", "/api/v2/spot/trade/orderInfo", authenticated=True, params=params
        )

        data_list = response.get("data", [])
        if data_list:
            return self._parse_order_result(data_list[0], symbol)

        raise ExchangeError(f"Bitget order not found: {exchange_order_id}")

    async def fetch_all_symbols(self) -> list[SymbolInfo]:
        """Fetch all tradeable symbols from Bitget.

        Returns:
            List of SymbolInfo for all symbols.

        Raises:
            ExchangeError: If the request fails.
        """
        response = await self._request("GET", "/api/v2/spot/public/symbols")

        symbols = []
        for sym in response.get("data", []):
            exchange_symbol = sym.get("symbol", "")
            base = sym.get("baseCoin", "")
            quote = sym.get("quoteCoin", "")

            # Convert BTCUSDT to generic BTC/USDT
            generic_symbol = f"{base}/{quote}" if base and quote else exchange_symbol

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

    def _parse_order_result(self, data: dict, symbol: str) -> OrderResult:
        """Parse a Bitget order response into an OrderResult.

        Args:
            data: Raw Bitget order response.
            symbol: Trading pair symbol in generic format.

        Returns:
            OrderResult parsed from the response.
        """
        status = data.get("status", "")
        order_status = self._map_status(status)

        return OrderResult(
            order_id=str(data.get("orderId", "")),
            exchange_order_id=str(data.get("orderId", "")),
            exchange=self.name,
            symbol=symbol,
            side=data.get("side", "").lower(),
            order_type=data.get("orderType", "").lower(),
            price=Decimal(data.get("price", "0")) if data.get("price") else None,
            volume=Decimal(data.get("size", "0")),
            filled_volume=Decimal(data.get("filledQty", "0")),
            status=order_status,
            created_at_ms=int(data.get("cTime", 0)),
            updated_at_ms=int(data.get("uTime", 0)),
        )

    def _map_status(self, status: str) -> str:
        """Map Bitget order status to standardized status.

        Args:
            status: Bitget order status string.

        Returns:
            Standardized status string.
        """
        status_mapping = {
            "new": "submitted",
            "open": "submitted",
            "filled": "filled",
            "partially_filled": "partially_filled",
            "cancelled": "cancelled",
            "canceled": "cancelled",
        }
        return status_mapping.get(status.lower(), status)

    async def close(self) -> None:
        """Close the adapter and release resources."""
        await self._close_session()