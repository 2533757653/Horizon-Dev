"""Order Manager with SQLite persistence and event logging."""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Optional

import aiosqlite

from ..exchange.registry import ExchangeRegistry
from ..exchange.types import OrderResult


class OrderSubmissionError(Exception):
    """Raised when order submission fails."""

    pass


class OrderManager:
    """Manages order submission, tracking, and synchronization with exchanges."""

    # Order source constants
    MANUAL = "manual"
    LLM_PROPOSAL = "llm_proposal"
    AUTONOMOUS = "autonomous"

    # Valid statuses for open orders
    OPEN_STATUSES = ("pending", "submitted", "partially_filled")

    @dataclass
    class OrderRequest:
        """Request to submit a new order."""

        exchange: str
        symbol: str
        side: Literal["buy", "sell"]
        order_type: Literal["market", "limit"]
        price: Decimal | None
        volume: Decimal
        source: str
        proposal_id: str | None = None

    def __init__(self, registry: ExchangeRegistry, db: aiosqlite.Connection, active_exchange: str):
        """Initialize the OrderManager.

        Args:
            registry: Exchange registry for accessing exchange adapters.
            db: Async SQLite database connection.
            active_exchange: Name of the active exchange for order validation.
        """
        self._registry = registry
        self._db = db
        self._active_exchange = active_exchange
        self._open_orders: dict[str, OrderResult] = {}
        self._lock = asyncio.Lock()
        self._sync_task: asyncio.Task | None = None
        self._order_sync_interval_seconds: int = 30

    async def submit_order(self, request: OrderRequest) -> OrderResult:
        """Submit a new order to an exchange.

        Args:
            request: Order request details.

        Returns:
            OrderResult with the submitted order details.

        Raises:
            OrderSubmissionError: If the order cannot be submitted.
        """
        async with self._lock:
            # Validate exchange exists and adapter is enabled
            adapter = self._registry.get(request.exchange)
            if adapter is None:
                raise OrderSubmissionError(f"Exchange '{request.exchange}' not found in registry")

            if not adapter.enabled:
                raise OrderSubmissionError(f"Exchange '{request.exchange}' is not enabled")

            # Validate exchange is the active exchange
            if request.exchange != self._active_exchange:
                raise OrderSubmissionError(
                    f"Exchange '{request.exchange}' is not the active exchange. "
                    f"Current active exchange: '{self._active_exchange}'. "
                    f"Submit orders to '{self._active_exchange}' only."
                )

            # Generate UUID v4 as internal order ID
            internal_order_id = str(uuid.uuid4())

            # Determine initial status
            initial_status = "pending"

            # Insert into orders table with status "pending"
            now_ms = self._get_current_timestamp_ms()
            await self._db.execute(
                """
                INSERT INTO orders (id, exchange, symbol, side, order_type, price, volume,
                                   filled_volume, status, source, proposal_id, exchange_order_id,
                                   created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    internal_order_id,
                    request.exchange,
                    request.symbol,
                    request.side,
                    request.order_type,
                    float(request.price) if request.price else None,
                    float(request.volume),
                    initial_status,
                    request.source,
                    request.proposal_id,
                    now_ms,
                    now_ms,
                ),
            )
            await self._db.commit()

            # Record event with event_type="created"
            await self.record_event(internal_order_id, "created", {"order_request": {
                "exchange": request.exchange,
                "symbol": request.symbol,
                "side": request.side,
                "order_type": request.order_type,
                "price": str(request.price) if request.price else None,
                "volume": str(request.volume),
            }})

            # Call adapter's place_market_order or place_limit_order
            try:
                if request.order_type == "market":
                    result = await adapter.place_market_order(
                        request.symbol, request.side, request.volume
                    )
                else:
                    if request.price is None:
                        raise OrderSubmissionError("Limit order requires a price")
                    result = await adapter.place_limit_order(
                        request.symbol, request.side, request.price, request.volume
                    )
            except Exception as e:
                # On failure: update orders row to status="rejected"
                await self._db.execute(
                    "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
                    ("rejected", self._get_current_timestamp_ms(), internal_order_id),
                )
                await self._db.commit()

                # Record event_type="rejected" with error in event_data
                await self.record_event(
                    internal_order_id,
                    "rejected",
                    {"error": str(e), "error_type": type(e).__name__}
                )

                raise OrderSubmissionError(f"Order submission failed: {e}") from e

            # On success: update orders row with exchange_order_id and status="submitted"
            await self._db.execute(
                """
                UPDATE orders
                SET exchange_order_id = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (result.order_id, "submitted", self._get_current_timestamp_ms(), internal_order_id),
            )
            await self._db.commit()

            # Record event_type="submitted"
            await self.record_event(internal_order_id, "submitted", {
                "exchange_order_id": result.order_id,
            })

            # Create updated OrderResult with our internal ID as order_id
            updated_result = OrderResult(
                order_id=internal_order_id,
                exchange_order_id=result.order_id,
                exchange=request.exchange,
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                price=result.price,
                volume=result.volume,
                filled_volume=result.filled_volume,
                status="submitted",
                created_at_ms=now_ms,
                updated_at_ms=self._get_current_timestamp_ms(),
            )

            # Add to _open_orders
            self._open_orders[internal_order_id] = updated_result

            return updated_result

    async def cancel_order(self, internal_order_id: str) -> bool:
        """Cancel an open order.

        Args:
            internal_order_id: Internal order ID to cancel.

        Returns:
            True if cancellation was successful, False otherwise.
        """
        async with self._lock:
            # Look up in _open_orders
            order = self._open_orders.get(internal_order_id)
            if order is None:
                return False

            # Get the adapter for this exchange
            adapter = self._registry.get(order.exchange)
            if adapter is None or not adapter.enabled:
                return False

            try:
                # Call adapter.cancel_order
                success = await adapter.cancel_order(order.exchange_order_id, order.symbol)
            except Exception:
                success = False

            if success:
                # Update status to "cancelled"
                await self._db.execute(
                    "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
                    ("cancelled", self._get_current_timestamp_ms(), internal_order_id),
                )
                await self._db.commit()

                # Record event
                await self.record_event(internal_order_id, "cancelled", {
                    "exchange_order_id": order.exchange_order_id,
                })

                # Remove from _open_orders
                del self._open_orders[internal_order_id]

            return success

    async def get_open_orders(
        self, exchange: str | None = None, symbol: str | None = None
    ) -> list[OrderResult]:
        """Get all open orders, optionally filtered.

        Args:
            exchange: Optional exchange name to filter by.
            symbol: Optional symbol to filter by.

        Returns:
            List of OrderResult for open orders matching filters.
            Only orders with status in (pending, submitted, partially_filled) are returned.
        """
        async with self._lock:
            result = []
            for order in self._open_orders.values():
                if order.status not in self.OPEN_STATUSES:
                    continue
                if exchange is not None and order.exchange != exchange:
                    continue
                if symbol is not None and order.symbol != symbol:
                    continue
                result.append(order)
            return result

    async def get_order_history(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get historical orders from the database.

        Args:
            exchange: Optional exchange name to filter by.
            symbol: Optional symbol to filter by.
            limit: Maximum number of orders to return (default 100).

        Returns:
            List of order dictionaries from the database.
        """
        query = "SELECT * FROM orders WHERE 1=1"
        params = []

        if exchange is not None:
            query += " AND exchange = ?"
            params.append(exchange)

        if symbol is not None:
            query += " AND symbol = ?"
            params.append(symbol)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()

        return [dict(row) for row in rows]

    async def start_sync_loop(self) -> None:
        """Start the background sync loop to update order statuses from exchanges."""
        if self._sync_task is not None and not self._sync_task.done():
            return

        self._sync_task = asyncio.create_task(self._sync_open_orders_loop())

    async def _sync_open_orders_loop(self) -> None:
        """Background task that periodically syncs open orders with exchange state."""
        while True:
            try:
                await asyncio.sleep(self._order_sync_interval_seconds)
                await self._sync_open_orders()
            except asyncio.CancelledError:
                break
            except Exception:
                # Log error but continue running
                pass

    async def _sync_open_orders(self) -> None:
        """Sync open orders with exchange state for the active exchange only."""
        async with self._lock:
            active_adapter = self._registry.get_active_adapter(self._active_exchange)
            if active_adapter is None:
                return

            # Collect orders for active exchange only
            orders = [
                order for order in self._open_orders.values()
                if order.status in self.OPEN_STATUSES and order.exchange == self._active_exchange
            ]

            if not orders:
                return

            try:
                exchange_orders = await active_adapter.fetch_open_orders()
                exchange_order_map = {o.exchange_order_id: o for o in exchange_orders}

                for local_order in orders:
                    exchange_order = exchange_order_map.get(local_order.exchange_order_id)

                    if exchange_order is None:
                        if local_order.status in self.OPEN_STATUSES:
                            await self._db.execute(
                                "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
                                ("filled", self._get_current_timestamp_ms(), local_order.order_id),
                            )
                            await self._db.commit()
                            await self.record_event(
                                local_order.order_id,
                                "filled",
                                {"reason": "order_not_found_on_exchange"}
                            )
                            del self._open_orders[local_order.order_id]
                    else:
                        if exchange_order.filled_volume > local_order.filled_volume:
                            event_type = "partial_fill" if exchange_order.filled_volume < exchange_order.volume else "filled"
                            await self.record_event(
                                local_order.order_id,
                                event_type,
                                {
                                    "filled_volume": str(exchange_order.filled_volume),
                                    "status": exchange_order.status,
                                }
                            )

                        self._open_orders[local_order.order_id] = exchange_order
                        await self._db.execute(
                            """
                            UPDATE orders
                            SET filled_volume = ?, status = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                float(exchange_order.filled_volume),
                                exchange_order.status,
                                self._get_current_timestamp_ms(),
                                local_order.order_id,
                            ),
                        )
                        await self._db.commit()

            except Exception:
                pass

    async def record_event(
        self, order_id: str, event_type: str, event_data: dict | None = None
    ) -> None:
        """Record an event for an order.

        Args:
            order_id: The internal order ID.
            event_type: Type of event (created, submitted, filled, etc.).
            event_data: Optional dictionary of additional event data.
        """
        # JSON-serialize event_data if dict
        event_data_json = json.dumps(event_data) if event_data is not None else None

        await self._db.execute(
            """
            INSERT INTO order_events (order_id, event_type, event_data, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (order_id, event_type, event_data_json, self._get_current_timestamp_ms()),
        )
        await self._db.commit()

    async def stop(self) -> None:
        """Stop the order manager and cancel any background tasks."""
        if self._sync_task is not None and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None

    @staticmethod
    def _get_current_timestamp_ms() -> int:
        """Get current timestamp in milliseconds."""
        import time
        return int(time.time() * 1000)