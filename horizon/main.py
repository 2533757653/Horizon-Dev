"""Horizon Trading Platform - Main Entry Point.

Wires all components together with correct startup/shutdown sequence.
"""

import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import uvicorn
from fastapi import FastAPI

from horizon.internal.config.settings import settings
from horizon.internal.database.db import close_db, init_db
from horizon.internal.exchange.binance import BinanceAdapter
from horizon.internal.exchange.bitget import BitgetAdapter
from horizon.internal.exchange.hyperliquid import HyperliquidAdapter
from horizon.internal.exchange.htx import HTXAdapter
from horizon.internal.exchange.registry import ExchangeRegistry
from horizon.internal.marketdata.fetcher import MarketDataFetcher
from horizon.internal.ordermanager.manager import OrderManager
from horizon.internal.pairlist import PairListRegistry, SymbolCache
from horizon.internal.pairlist.base import PairList
from horizon.internal.portfolio.tracker import PortfolioTracker


def _setup_logging() -> None:
    """Configure Python logging with structured format."""
    log_level = getattr(logging, settings.app.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def _create_minimal_app() -> FastAPI:
    """Create a minimal FastAPI app when web server module is not available."""
    application = FastAPI(
        title="Horizon Trading Platform",
        description="Quantitative Cryptocurrency Trading System",
        version="0.1.0",
    )

    @application.get("/api/health")
    async def health_check() -> dict[str, Any]:
        """Health check endpoint."""
        return {"status": "ok", "timestamp": int(time.time())}

    @application.get("/api/portfolio")
    async def get_portfolio() -> dict[str, Any]:
        """Get current portfolio snapshot (placeholder when web server unavailable)."""
        return {
            "exchanges": {},
            "total_usdt_value": "0",
            "timestamp_ms": 0,
        }

    @application.get("/api/orders")
    async def get_orders() -> dict[str, Any]:
        """Get open orders (placeholder when web server unavailable)."""
        return []

    @application.get("/api/market-data/{symbol}")
    async def get_market_data(symbol: str) -> dict[str, Any]:
        """Get market data for a symbol (placeholder when web server unavailable)."""
        return {
            "symbol": symbol,
            "tickers": [],
        }

    @application.get("/api/market-data/stream")
    async def market_data_stream() -> dict[str, Any]:
        """SSE endpoint placeholder."""
        return {"message": "SSE not available in minimal mode"}

    @application.post("/api/orders")
    async def submit_order() -> dict[str, Any]:
        """Submit order placeholder."""
        return {"error": "Order submission not available in minimal mode"}

    return application


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager.

    Handles startup and shutdown events for the Horizon Trading Platform.
    """
    logger = logging.getLogger(__name__)

    # ---- Startup ----
    logger.info("Starting Horizon server...")

    # 1. Database initialization with migrations
    migrations_dir = os.path.join(os.path.dirname(__file__), "internal", "database", "migrations")
    db = await init_db(settings.database.db_path, migrations_dir)
    logger.info("Database initialized at %s", settings.database.db_path)

    # 2. Exchange registry setup
    registry = ExchangeRegistry()

    # Register Binance if enabled
    binance_config = settings.exchanges.binance
    if binance_config.enabled:
        adapter = BinanceAdapter(
            api_key=binance_config.api_key or "",
            api_secret=binance_config.api_secret or "",
            recv_window_ms=binance_config.recv_window_ms,
        )
        registry.register(adapter)
        logger.info("Binance adapter registered (enabled=%s)", adapter.enabled)

    # Register HTX if enabled
    htx_config = settings.exchanges.htx
    if htx_config.enabled:
        adapter = HTXAdapter(
            api_key=htx_config.api_key or "",
            api_secret=htx_config.api_secret or "",
            recv_window_ms=htx_config.recv_window_ms,
        )
        registry.register(adapter)
        logger.info("HTX adapter registered (enabled=%s)", adapter.enabled)

    # Register Hyperliquid if enabled
    hyperliquid_config = settings.exchanges.hyperliquid
    if hyperliquid_config.enabled:
        adapter = HyperliquidAdapter(
            wallet_address=hyperliquid_config.wallet_address or "",
            private_key=hyperliquid_config.private_key or "",
        )
        registry.register(adapter)
        logger.info("Hyperliquid adapter registered (enabled=%s)", adapter.enabled)

    # Register Bitget if enabled
    bitget_config = settings.exchanges.bitget
    if bitget_config.enabled:
        adapter = BitgetAdapter(
            api_key=bitget_config.api_key or "",
            api_secret=bitget_config.api_secret or "",
            passphrase=bitget_config.passphrase or "",
        )
        registry.register(adapter)
        logger.info("Bitget adapter registered (enabled=%s)", adapter.enabled)

    # 2a. Initialize PairList registry and discover plugins
    pairlist_registry = PairListRegistry()
    pairlist_registry.discover("horizon.pairlists")
    logger.info("PairList plugins discovered: %s", [p["name"] for p in pairlist_registry.list_all()])

    # 2b. Initialize symbol cache
    symbol_cache = SymbolCache(db)

    # 2c. Set active exchange from settings (single exchange mode)
    active_exchange = settings.exchanges.active_exchange
    logger.info("Active exchange: %s", active_exchange)

    # 2d. Refresh symbol cache from active exchange on startup
    adapter = registry.get(active_exchange)
    if adapter and adapter.enabled:
        try:
            await asyncio.wait_for(
                symbol_cache.refresh_active_exchange(adapter),
                timeout=10.0,
            )
            logger.info("Symbol cache refreshed from active exchange: %s", active_exchange)
        except asyncio.TimeoutError:
            logger.warning("Symbol cache refresh timed out for %s", active_exchange)
        except Exception as e:
            logger.warning("Symbol cache refresh failed for %s: %s", active_exchange, e)
    else:
        logger.warning("Active exchange '%s' not found or not enabled", active_exchange)
    logger.info("Symbol cache populated from active exchange: %s", active_exchange)

    # 2e. Set default active PairList (first discovered)
    available = pairlist_registry.list_all()
    if available:
        default_pairlist_name = available[0]["name"]
        active_pairlist = pairlist_registry.create(default_pairlist_name)
        logger.info("Default PairList: %s", default_pairlist_name)
    else:
        active_pairlist = None
        logger.warning("No PairList plugins found!")

    # 3. Market data fetcher setup
    fetcher = MarketDataFetcher(
        registry=registry,
        db=db,
        active_pairlist=active_pairlist,
        active_exchange=active_exchange,
        poll_interval_seconds=settings.trading.market_data_poll_interval_seconds,
    )

    # 4. Portfolio tracker setup
    portfolio_tracker = PortfolioTracker(
        registry=registry,
        db=db,
        fetcher=fetcher,
        active_exchange=active_exchange,
        snapshot_interval_seconds=settings.trading.portfolio_snapshot_interval_seconds,
    )

    # 5. Order manager setup
    order_manager = OrderManager(
        registry=registry,
        db=db,
        active_exchange=active_exchange,
    )

    # Store components in app state
    application.state.db = db
    application.state.registry = registry
    application.state.fetcher = fetcher
    application.state.order_manager = order_manager
    application.state.portfolio_tracker = portfolio_tracker
    application.state.pairlist_registry = pairlist_registry
    application.state.active_pairlist = active_pairlist
    application.state.active_exchange = active_exchange
    application.state.symbol_cache = symbol_cache

    # 6. Start background tasks
    await fetcher.start()
    await portfolio_tracker.start()
    await order_manager.start_sync_loop()
    logger.info("All background tasks started")

    logger.info("Horizon server started on %s:%s", settings.app.host, settings.app.port)

    yield

    # ---- Shutdown ----
    logger.info("Stopping Horizon server...")

    # Stop fetcher
    await fetcher.stop()
    logger.info("Market data fetcher stopped")

    # Stop portfolio tracker
    await portfolio_tracker.stop()
    logger.info("Portfolio tracker stopped")

    # Stop order manager
    await order_manager.stop()
    logger.info("Order manager stopped")

    # Close database
    await close_db(db)
    logger.info("Database connection closed")

    logger.info("Horizon server stopped")


def _create_app() -> FastAPI:
    """Create the FastAPI application with lifespan.

    Attempts to import create_app from internal.web.server, falls back to minimal app.

    Returns:
        Configured FastAPI application instance.
    """
    # Try to use the full web server create_app
    try:
        from horizon.internal.web.server import create_app as _create_app_func

        # Create database directory if needed
        db_path = settings.database.db_path
        parent_dir = os.path.dirname(db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        # Initialize database synchronously with migrations
        migrations_dir = os.path.join(os.path.dirname(__file__), "internal", "database", "migrations")
        db = asyncio.run(init_db(db_path, migrations_dir))

        # Create registry and register adapters
        registry = ExchangeRegistry()

        # Register adapters
        binance_config = settings.exchanges.binance
        if binance_config.enabled:
            adapter = BinanceAdapter(
                api_key=binance_config.api_key or "",
                api_secret=binance_config.api_secret or "",
                recv_window_ms=binance_config.recv_window_ms,
            )
            registry.register(adapter)

        htx_config = settings.exchanges.htx
        if htx_config.enabled:
            adapter = HTXAdapter(
                api_key=htx_config.api_key or "",
                api_secret=htx_config.api_secret or "",
                recv_window_ms=htx_config.recv_window_ms,
            )
            registry.register(adapter)

        hyperliquid_config = settings.exchanges.hyperliquid
        if hyperliquid_config.enabled:
            adapter = HyperliquidAdapter(
                wallet_address=hyperliquid_config.wallet_address or "",
                private_key=hyperliquid_config.private_key or "",
            )
            registry.register(adapter)

        bitget_config = settings.exchanges.bitget
        if bitget_config.enabled:
            adapter = BitgetAdapter(
                api_key=bitget_config.api_key or "",
                api_secret=bitget_config.api_secret or "",
                passphrase=bitget_config.passphrase or "",
            )
            registry.register(adapter)

        # Create components
        pairlist_registry = PairListRegistry()
        pairlist_registry.discover("horizon.pairlists")
        symbol_cache = SymbolCache(db)
        active_exchange = settings.exchanges.active_exchange

        available = pairlist_registry.list_all()
        active_pairlist = pairlist_registry.create(available[0]["name"]) if available else None

        fetcher = MarketDataFetcher(
            registry=registry,
            db=db,
            active_pairlist=active_pairlist,
            active_exchange=active_exchange,
            poll_interval_seconds=settings.trading.market_data_poll_interval_seconds,
        )

        portfolio_tracker = PortfolioTracker(
            registry=registry,
            db=db,
            fetcher=fetcher,
            active_exchange=active_exchange,
            snapshot_interval_seconds=settings.trading.portfolio_snapshot_interval_seconds,
        )

        order_manager = OrderManager(
            registry=registry,
            db=db,
            active_exchange=active_exchange,
        )

        # Call the web server's create_app - this returns an app with all endpoints
        application = _create_app_func(
            settings=settings,
            db=db,
            registry=registry,
            fetcher=fetcher,
            order_manager=order_manager,
            portfolio_tracker=portfolio_tracker,
        )

        # Override lifespan with our custom one for startup/shutdown
        application.router.lifespan_context = lifespan

        return application

    except (ImportError, Exception) as e:
        logging.warning(f"Could not load full web server, using minimal app: {e}")
        import traceback
        traceback.print_exc()
        # Fall back to minimal app
        application = _create_minimal_app()
        application.router.lifespan_context = lifespan
        return application


# Create module-level app instance
app = _create_app()


def main() -> None:
    """Run the Horizon Trading Platform server."""
    _setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Launching Horizon Trading Platform...")

    uvicorn.run(
        "horizon.main:app",
        host=settings.app.host,
        port=settings.app.port,
        log_level=settings.app.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()