"""Portfolio Tracker with USDT valuation and historical snapshots."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

import aiosqlite

from ..exchange.registry import ExchangeRegistry
from ..exchange.types import Balance

logger = logging.getLogger(__name__)


class PortfolioTracker:
    """Tracks portfolio balances and USDT valuation across exchanges."""

    # Stablecoins valued at face value (1:1 USD)
    STABLECOINS = {"USDC", "USDT", "USDE", "USDH"}

    # Assets excluded from USDT valuation. We only trade USD-futures, so
    # exchange-native spot tokens (e.g. HYPE on Hyperliquid) are not part of
    # the trading balance and should not be added to total_usdt_value.
    EXCLUDED_ASSETS = {"HYPE"}

    @dataclass
    class PortfolioSnapshot:
        """A snapshot of portfolio balances and total value."""

        exchanges: dict[str, list[Balance]] = field(default_factory=dict)
        total_usdt_value: Decimal = Decimal("0")
        timestamp_ms: int = 0

    def __init__(
        self,
        registry: ExchangeRegistry,
        db: aiosqlite.Connection,
        fetcher: Any,
        active_exchange: str,
        snapshot_interval_seconds: int = 60,
    ) -> None:
        """Initialize the portfolio tracker.

        Args:
            registry: Exchange registry for accessing adapters.
            db: Async SQLite database connection.
            fetcher: MarketDataFetcher instance for getting ticker prices.
            active_exchange: Name of the active exchange to use.
            snapshot_interval_seconds: Interval between snapshots in seconds.
        """
        self._registry = registry
        self._db = db
        self._fetcher = fetcher
        self._active_exchange = active_exchange
        self._snapshot_interval = snapshot_interval_seconds
        self._balances: dict[str, dict[str, Balance]] = {}
        self._snapshot_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the portfolio tracker.

        Immediately refreshes balances and starts the background snapshot loop.
        """
        # Initial refresh of balances
        await self._refresh_balances()

        # Start background snapshot loop
        self._stop_event = asyncio.Event()
        self._snapshot_task = asyncio.create_task(self._snapshot_loop())
        logger.info("Portfolio tracker started with interval %ds", self._snapshot_interval)

    async def _snapshot_loop(self) -> None:
        """Background loop that periodically saves portfolio snapshots.

        Sleeps in small (0.5s) chunks so `_stop_event` is checked
        frequently even if `cancel()` is missed for any reason. This is
        defense-in-depth on top of `task.cancel()` in `stop()`.
        """
        chunk = 0.5
        while not self._stop_event.is_set():
            try:
                # Sleep for the configured interval, checking stop_event
                elapsed = 0.0
                while elapsed < self._snapshot_interval and not self._stop_event.is_set():
                    await asyncio.sleep(chunk)
                    elapsed += chunk

                if self._stop_event.is_set():
                    break

                # Refresh balances
                await self._refresh_balances()

                # Save snapshot to database
                await self.save_snapshot()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Error in snapshot loop: %s", e)

    async def _refresh_balances(self) -> None:
        """Refresh balances from the active exchange only."""
        active_adapter = self._registry.get_active_adapter(self._active_exchange)
        if active_adapter is None:
            logger.warning(
                "No active adapter for exchange '%s' - skipping balance fetch",
                self._active_exchange,
            )
            return

        try:
            balances = await active_adapter.fetch_all_balances()
            async with self._lock:
                self._balances[active_adapter.name] = {balance.asset: balance for balance in balances}
        except Exception as e:
            logger.warning(
                "Failed to fetch balances from %s: %s",
                active_adapter.name,
                e,
            )

    async def get_snapshot(self) -> PortfolioSnapshot:
        """Get a snapshot of the current portfolio state.

        Returns:
            PortfolioSnapshot with current balances and total USDT value.
        """
        async with self._lock:
            # Copy balances structure
            exchanges: dict[str, list[Balance]] = {}
            for exchange_name, asset_balances in self._balances.items():
                exchanges[exchange_name] = list(asset_balances.values())

        # Calculate USDT values
        total_usdt_value = Decimal("0")

        for exchange_name, balances in exchanges.items():
            for balance in balances:
                if balance.asset in self.EXCLUDED_ASSETS:
                    # Excluded from valuation (e.g. HYPE on Hyperliquid since
                    # we only trade USD-futures).
                    value = Decimal("0")
                elif balance.asset in self.STABLECOINS:
                    # Stablecoins valued at face value
                    value = balance.free + balance.locked
                else:
                    # Look up ticker for conversion to USDT
                    ticker = self._fetcher.get_ticker(balance.asset + "USDT")
                    if ticker is not None:
                        value = (balance.free + balance.locked) * ticker.price
                    else:
                        # No ticker available, treat as zero value
                        value = Decimal("0")
                        logger.debug(
                            "No ticker for %sUSDT, treating as zero value",
                            balance.asset,
                        )

                total_usdt_value += value

        timestamp_ms = int(time.time() * 1000)

        return self.PortfolioSnapshot(
            exchanges=exchanges,
            total_usdt_value=total_usdt_value,
            timestamp_ms=timestamp_ms,
        )

    async def save_snapshot(self) -> None:
        """Save the current portfolio snapshot to the database."""
        snapshot = await self.get_snapshot()

        # Use a transaction for atomicity
        await self._db.execute("BEGIN TRANSACTION")
        try:
            for exchange_name, balances in snapshot.exchanges.items():
                for balance in balances:
                    # Calculate USDT value for this balance
                    if balance.asset in self.EXCLUDED_ASSETS:
                        usdt_value = Decimal("0")
                    elif balance.asset in self.STABLECOINS:
                        usdt_value = balance.free + balance.locked
                    else:
                        ticker = self._fetcher.get_ticker(balance.asset + "USDT")
                        if ticker is not None:
                            usdt_value = (balance.free + balance.locked) * ticker.price
                        else:
                            usdt_value = Decimal("0")

                    await self._db.execute(
                        """
                        INSERT INTO portfolio_snapshots
                        (exchange, asset, free_balance, locked_balance, usdt_value, snapshot_time)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            exchange_name,
                            balance.asset,
                            float(balance.free),
                            float(balance.locked),
                            float(usdt_value),
                            snapshot.timestamp_ms,
                        ),
                    )
            await self._db.execute("COMMIT")
        except Exception:
            await self._db.execute("ROLLBACK")
            raise

    async def get_historical_snapshots(
        self,
        exchange: Optional[str] = None,
        asset: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get historical portfolio snapshots from the database.

        Args:
            exchange: Optional exchange name to filter by.
            asset: Optional asset name to filter by.
            limit: Maximum number of rows to return (default 100).

        Returns:
            List of snapshot dictionaries from the database.
        """
        query = "SELECT * FROM portfolio_snapshots WHERE 1=1"
        params: list[Any] = []

        if exchange is not None:
            query += " AND exchange = ?"
            params.append(exchange)

        if asset is not None:
            query += " AND asset = ?"
            params.append(asset)

        query += " ORDER BY snapshot_time DESC LIMIT ?"
        params.append(limit)

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()

        return [dict(row) for row in rows]

    async def stop(self) -> None:
        """Stop the portfolio tracker and save a final snapshot.

        Cancels the running task so any in-flight `asyncio.sleep` is
        interrupted immediately — we don't wait for the full snapshot
        interval to elapse before returning.
        """
        if self._stop_event is not None:
            self._stop_event.set()

        if self._snapshot_task is not None:
            self._snapshot_task.cancel()
            try:
                await self._snapshot_task
            except asyncio.CancelledError:
                pass
            self._snapshot_task = None

        # Save a final snapshot
        try:
            await self.save_snapshot()
            logger.info("Portfolio tracker stopped, final snapshot saved")
        except Exception as e:
            logger.warning("Failed to save final snapshot: %s", e)