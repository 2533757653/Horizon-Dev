"""HTX (Huobi) exchange adapter."""

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
    Ticker,
)

from . import ExchangeError


class HTXAdapter(ExchangeAdapter):
    """HTX (Huobi) exchange adapter.

    Implements the ExchangeAdapter interface for the HTX exchange.
    """

    BASE_URL = "https://api.huobi.pro"
    TIMEOUT_SECONDS = 10

    def __init__(
        self, api_key: str, api_secret: str, recv_window_ms: int = 5000
    ):
        """Initialize the HTX adapter.

        Args:
            api_key: HTX API key.
            api_secret: HTX API secret.
            recv_window_ms: Receive window in milliseconds for signed requests.
        """
        self._api_key = api_key
        self._api_secret = api_secret
        self._recv_window_ms = recv_window_ms
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def name(self) -> str:
        """Return the exchange name."""
        return "htx"

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

    def _format_timestamp(self) -> str:
        """Format current UTC time as ISO 8601 timestamp.

        Returns:
            Timestamp in ISO 8601 format (YYYY-MM-DDTHH:MM:SS).
        """
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

    def _sign_request(self, method: str, host: str, path: str,
                      sorted_params: str) -> str:
        """Generate HMAC-SHA256 signature for HTX API v2.

        Args:
            method: HTTP method (GET, POST).
            host: API host (e.g., 'api.huobi.pro').
            path: API endpoint path (e.g., '/v1/account/accounts').
            sorted_params: Sorted query string (without leading '?').

        Returns:
            Base64-encoded HMAC-SHA256 signature.
        """
        # Build canonical request: METHOD\nhost\npath\nsorted-params
        canonical_request = f"{method}\n{host}\n{path}\n{sorted_params}"

        # HMAC-SHA256 with base64-encoded secret
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            canonical_request.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        return base64.b64encode(signature).decode("utf-8")

    def _prepare_auth_params(self) -> dict:
        """Prepare common authentication parameters for API v2.

        Returns:
            Dictionary with authentication parameters.
        """
        timestamp = self._format_timestamp()
        return {
            "AccessKeyId": self._api_key,
            "SignatureMethod": "HmacSHA256",
            "SignatureVersion": "2",
            "Timestamp": timestamp,
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        authenticated: bool = False,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> dict:
        """Make an HTTP request to the HTX API.

        Args:
            method: HTTP method (GET, POST).
            endpoint: API endpoint path.
            authenticated: Whether the request requires authentication.
            params: Query parameters.
            data: JSON body for POST requests.

        Returns:
            JSON response as dictionary.

        Raises:
            ExchangeError: If the request fails.
        """
        url = f"{self.BASE_URL}{endpoint}"
        headers = {"Content-Type": "application/json"}

        if authenticated:
            # Add auth params
            all_params = dict(params) if params else {}
            all_params.update(self._prepare_auth_params())

            # Sort params for signature
            sorted_params = "&".join(
                f"{k}={v}" for k, v in sorted(all_params.items())
            )

            # Generate signature
            signature = self._sign_request(
                method.upper(), "api.huobi.pro", endpoint, sorted_params
            )
            all_params["Signature"] = signature

            params = all_params

        try:
            session = await self._get_session()
            async with session.request(
                method,
                url,
                params=params,
                json=data if data else None,
                headers=headers,
            ) as response:
                if response.status >= 400:
                    error_text = await response.text()
                    try:
                        error_data = await response.json()
                        # Check for HTX error format
                        if "err-msg" in error_data:
                            raise ExchangeError(
                                f"HTX {error_data.get('err-code', 'error')}: "
                                f"{error_data.get('err-msg', error_text)}"
                            )
                        raise ExchangeError(
                            f"HTX HTTP {response.status}: {error_text}"
                        )
                    except Exception:
                        raise ExchangeError(
                            f"HTX HTTP {response.status}: {error_text}"
                        )

                result = await response.json()

                # Check for HTX API error
                if isinstance(result, dict):
                    if result.get("status") == "error":
                        err_code = result.get("err-code", "unknown")
                        err_msg = result.get("err-msg", "Unknown error")
                        raise ExchangeError(f"HTX {err_code}: {err_msg}")

                return result

        except aiohttp.ClientError as e:
            raise ExchangeError(str(e)) from e

    def _normalize_symbol(self, symbol: str) -> str:
        """Convert generic symbol format to HTX format.

        Args:
            symbol: Symbol in generic format (e.g., 'BTC/USDT').

        Returns:
            HTX symbol format (e.g., 'btcusdt').
        """
        return symbol.replace("/", "").lower()

    def _denormalize_symbol(self, symbol: str) -> str:
        """Convert HTX symbol format to generic format.

        Args:
            symbol: Symbol in HTX format (e.g., 'btcusdt').

        Returns:
            Generic symbol format (e.g., 'BTC/USDT').
        """
        symbol_upper = symbol.upper()
        if len(symbol_upper) > 3:
            # Assume base is first 3-5 chars, quote is rest
            return f"{symbol_upper[:-4]}/{symbol_upper[-4:]}"
        return symbol_upper

    async def fetch_ticker(self, symbol: str) -> Ticker:
        """Fetch current ticker for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT').

        Returns:
            Ticker with current price and volume information.

        Raises:
            ExchangeError: If the request fails.
        """
        htx_symbol = self._normalize_symbol(symbol)
        params = {"symbol": htx_symbol}

        data = await self._request("GET", "/market/detail/merged", params=params)

        tick = data.get("tick", {})
        return Ticker(
            symbol=symbol,
            price=Decimal(str(tick.get("lastPrice", 0))),
            volume_24h=Decimal(str(tick.get("vol", 0))),
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
        htx_symbol = self._normalize_symbol(symbol)
        params = {"symbol": htx_symbol, "type": "step0"}

        data = await self._request("GET", "/market/depth", params=params)

        tick = data.get("tick", {})
        bids = [
            OrderBookEntry(
                price=Decimal(str(entry[0])), size=Decimal(str(entry[1]))
            )
            for entry in tick.get("bid", [])
        ]
        asks = [
            OrderBookEntry(
                price=Decimal(str(entry[0])), size=Decimal(str(entry[1]))
            )
            for entry in tick.get("ask", [])
        ]

        return OrderBook(
            symbol=symbol,
            exchange=self.name,
            bids=bids[:depth],
            asks=asks[:depth],
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
            if balance.asset == asset.upper():
                return balance

        return Balance(
            asset=asset.upper(),
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
        # First get account list
        accounts_data = await self._request(
            "GET", "/v1/account/accounts", authenticated=True
        )

        balances = []

        # Iterate through accounts (spot, margin, etc.)
        for account in accounts_data:
            account_id = account.get("id")
            account_type = account.get("type", "spot")

            # Only fetch balance for spot account for now
            if account_type == "spot":
                balance_data = await self._request(
                    "GET",
                    f"/v1/account/accounts/{account_id}/balance",
                    authenticated=True,
                )

                for balance_item in balance_data.get("list", []):
                    asset = balance_item.get("currency", "").upper()
                    if not asset:
                        continue

                    balance_type = balance_item.get("type", "trade")
                    balance = Decimal(str(balance_item.get("balance", 0)))

                    # Find or create balance for this asset
                    existing = None
                    for b in balances:
                        if b.asset == asset:
                            existing = b
                            break

                    if existing:
                        # Update existing balance
                        if balance_type == "trade":
                            balances = [
                                Balance(
                                    asset=existing.asset,
                                    free=balance,
                                    locked=existing.locked,
                                    exchange=existing.exchange,
                                )
                                if b.asset == asset and b.exchange == existing.exchange
                                else b
                                for b in balances
                            ]
                        elif balance_type == "frozen":
                            balances = [
                                Balance(
                                    asset=existing.asset,
                                    free=existing.free,
                                    locked=balance,
                                    exchange=existing.exchange,
                                )
                                if b.asset == asset and b.exchange == existing.exchange
                                else b
                                for b in balances
                            ]
                    else:
                        # Create new balance
                        free = Decimal("0")
                        locked = Decimal("0")
                        if balance_type == "trade":
                            free = balance
                        elif balance_type == "frozen":
                            locked = balance

                        balances.append(
                            Balance(
                                asset=asset,
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
        htx_symbol = self._normalize_symbol(symbol)
        order_data = {
            "account-id": "spot",
            "symbol": htx_symbol,
            "type": f"{side.lower()}-market",
            "amount": str(volume),
        }

        result = await self._request(
            "POST", "/v1/order/orders/place", authenticated=True, data=order_data
        )

        # HTX returns {"status": "ok", "data": "123456789"}
        exchange_order_id = str(result.get("data", ""))
        return OrderResult(
            order_id=exchange_order_id,
            exchange_order_id=exchange_order_id,
            exchange=self.name,
            symbol=symbol,
            side=side.lower(),
            order_type="market",
            price=None,
            volume=volume,
            filled_volume=Decimal("0"),
            status="submitted",
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
        htx_symbol = self._normalize_symbol(symbol)
        order_data = {
            "account-id": "spot",
            "symbol": htx_symbol,
            "type": f"{side.lower()}-limit",
            "amount": str(volume),
            "price": str(price),
        }

        result = await self._request(
            "POST", "/v1/order/orders/place", authenticated=True, data=order_data
        )

        exchange_order_id = str(result.get("data", ""))
        return OrderResult(
            order_id=exchange_order_id,
            exchange_order_id=exchange_order_id,
            exchange=self.name,
            symbol=symbol,
            side=side.lower(),
            order_type="limit",
            price=price,
            volume=volume,
            filled_volume=Decimal("0"),
            status="submitted",
            created_at_ms=int(time.time() * 1000),
            updated_at_ms=int(time.time() * 1000),
        )

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
        await self._request(
            "POST",
            f"/v1/order/orders/{exchange_order_id}/submitcancel",
            authenticated=True,
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
        params = {"states": "submitted,partial-filled"}
        if symbol:
            params["symbol"] = self._normalize_symbol(symbol)

        data = await self._request(
            "GET", "/v1/order/orders", authenticated=True, params=params
        )

        orders = data if isinstance(data, list) else data.get("data", [])

        return [
            self._parse_order_result(order_data)
            for order_data in orders
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
        data = await self._request(
            "GET",
            f"/v1/order/orders/{exchange_order_id}",
            authenticated=True,
        )

        return self._parse_order_result(data)

    def _parse_order_result(self, data: dict) -> OrderResult:
        """Parse an HTX order response into an OrderResult.

        Args:
            data: Raw HTX order response.

        Returns:
            OrderResult parsed from the response.
        """
        # Parse order type (e.g., "buy-limit" -> "limit", "buy-market" -> "market")
        order_type_full = data.get("type", "")
        parts = order_type_full.split("-")
        side = parts[0] if parts else "unknown"
        order_type = parts[1] if len(parts) > 1 else order_type_full

        # Parse symbol back to generic format
        symbol = data.get("symbol", "")
        generic_symbol = self._denormalize_symbol(symbol)

        # Map HTX states to standard status
        state = data.get("state", "")
        state_mapping = {
            "submitted": "submitted",
            "partial-filled": "partially_filled",
            "filled": "filled",
            "canceled": "cancelled",
            "rejected": "rejected",
        }
        status = state_mapping.get(state, state)

        # Parse amount and filled amount
        volume = Decimal(str(data.get("amount", 0)))
        filled_volume = Decimal(str(data.get("field-amount", 0)))
        price = Decimal(str(data.get("price", 0))) if data.get("price") else None

        # Get timestamps
        created_at = data.get("created-at", 0)
        updated_at = data.get("updated-at", created_at)

        return OrderResult(
            order_id=str(data.get("id", "")),
            exchange_order_id=str(data.get("id", "")),
            exchange=self.name,
            symbol=generic_symbol,
            side=side,
            order_type=order_type,
            price=price,
            volume=volume,
            filled_volume=filled_volume,
            status=status,
            created_at_ms=created_at,
            updated_at_ms=updated_at,
        )

    async def close(self) -> None:
        """Close the adapter and release resources."""
        await self._close_session()