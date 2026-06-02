"""Market data fetcher with SSE broadcasting."""

import asyncio
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

import aiosqlite

from ..exchange.registry import ExchangeRegistry
from ..exchange.types import Ticker

if TYPE_CHECKING:
    from ..pairlist.base import PairList

logger = logging.getLogger(__name__)


class MarketDataFetcher:
    """Fetches market data from exchanges and broadcasts updates via SSE."""

    def __init__(
        self,
        registry: ExchangeRegistry,
        db: aiosqlite.Connection,
        active_pairlist: "PairList",
        active_exchange: str,
        poll_interval_seconds: int = 10,
    ) -> None:
        """Initialize the market data fetcher.

        Args:
            registry: Exchange registry for accessing adapters.
            db: Database connection for PairList access.
            active_pairlist: PairList instance to get symbols from.
            active_exchange: Name of the active exchange to use.
            poll_interval_seconds: Interval between polling cycles.
        """
        self._registry = registry
        self._db = db
        self._active_pairlist = active_pairlist
        self._active_exchange = active_exchange
        self._symbols: list[str] = []
        self._poll_interval = poll_interval_seconds
        self._tickers: dict[str, dict[str, Ticker]] = {}
        self._subscribers: list[asyncio.Queue[dict]] = []
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """Start the background polling loop.

        Must be idempotent - calling start twice does NOT create duplicate tasks.
        """
        if self._task is not None and not self._task.done():
            return

        self._stop_event.clear()
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop the background polling loop and wait for completion."""
        self._stop_event.set()

        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _poll_loop(self) -> None:
        """Infinite polling loop that fetches data for all symbols."""
        while not self._stop_event.is_set():
            # Refresh symbol list from active PairList
            try:
                new_symbols = await self._active_pairlist.get_pairs(self._db)
            except Exception as e:
                logger.warning("PairList.get_pairs() failed: %s", e)
                new_symbols = []

            if set(new_symbols) != set(self._symbols):
                logger.info(
                    "PairList '%s' symbol list updated: %d -> %d symbols",
                    self._active_pairlist.name, len(self._symbols), len(new_symbols)
                )
                self._symbols = new_symbols

            for symbol in self._symbols:
                if self._stop_event.is_set():
                    break
                await self._fetch_symbol(symbol)

            await asyncio.sleep(self._poll_interval)

    async def _fetch_symbol(self, symbol: str) -> None:
        """Fetch ticker and orderbook for a symbol from the active exchange.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT').
        """
        active_adapter = self._registry.get_active_adapter(self._active_exchange)
        if active_adapter is None:
            logger.warning(
                "No active adapter for exchange '%s' - skipping fetch for %s",
                self._active_exchange,
                symbol,
            )
            return

        async def fetch_with_error_handling(fetch_fn, *args, **kwargs):
            try:
                return await fetch_fn(*args, **kwargs)
            except Exception as e:
                logger.warning(
                    "Failed to fetch %s from %s: %s",
                    symbol,
                    active_adapter.name,
                    e,
                )
                return None

        # Fetch ticker
        ticker = await fetch_with_error_handling(
            active_adapter.fetch_ticker, symbol
        )

        if ticker is not None:
            async with self._lock:
                if symbol not in self._tickers:
                    self._tickers[symbol] = {}
                self._tickers[symbol][active_adapter.name] = ticker

            await self._broadcast({
                "type": "ticker",
                "symbol": symbol,
                "exchange": active_adapter.name,
                "data": {
                    "symbol": ticker.symbol,
                    "price": str(ticker.price),
                    "volume_24h": str(ticker.volume_24h),
                    "exchange": ticker.exchange,
                    "timestamp_ms": ticker.timestamp_ms,
                },
            })

    async def _broadcast(self, message: dict) -> None:
        """Broadcast a message to all subscribers.

        Args:
            message: Message dict to broadcast.
        """
        for queue in self._subscribers:
            if queue.qsize() < 100:
                queue.put_nowait(message)

    def subscribe(self) -> asyncio.Queue[dict]:
        """Subscribe to market data updates.

        Returns:
            asyncio.Queue that will receive market data updates.
        """
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        """Unsubscribe from market data updates.

        Args:
            queue: The queue to remove from subscribers.
        """
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def get_ticker(
        self, symbol: str, exchange: str | None = None
    ) -> Ticker | None:
        """Get the latest ticker for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT').
            exchange: Optional exchange name. If None, returns first available.

        Returns:
            Latest ticker or None if not available.
        """
        if symbol not in self._tickers:
            return None

        if exchange is not None:
            return self._tickers[symbol].get(exchange)

        tickers = self._tickers[symbol]
        if not tickers:
            return None
        return next(iter(tickers.values()))

    def get_all_tickers(self, symbol: str) -> list[Ticker]:
        """Get all exchange tickers for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT').

        Returns:
            List of all tickers for the symbol across exchanges.
        """
        if symbol not in self._tickers:
            return []
        return list(self._tickers[symbol].values())

    async def persist_to_db(self, db: aiosqlite.Connection) -> None:
        """Write cached tickers to the market_data_cache table.

        Args:
            db: Database connection.
        """
        for symbol, exchange_tickers in self._tickers.items():
            for exchange, ticker in exchange_tickers.items():
                await db.execute(
                    """
                    INSERT OR REPLACE INTO market_data_cache
                    (exchange, symbol, last_price, bid_price, ask_price, volume_24h)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        exchange,
                        symbol,
                        str(ticker.price),
                        None,
                        None,
                        str(ticker.volume_24h),
                    ),
                )
        await db.commit()