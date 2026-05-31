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

import anthropic
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from horizon.internal.config.settings import load_keys_from_file, settings
from horizon.internal.database.db import close_db, init_db
from horizon.internal.exchange.binance import BinanceAdapter
from horizon.internal.exchange.bitget import BitgetAdapter
from horizon.internal.exchange.hyperliquid import HyperliquidAdapter
from horizon.internal.exchange.htx import HTXAdapter
from horizon.internal.exchange.registry import ExchangeRegistry
from horizon.internal.indicators.calculator import TechnicalIndicatorCalculator
from horizon.internal.llm.engine import LLMStrategyEngine
from horizon.internal.marketdata.fetcher import MarketDataFetcher
from horizon.internal.ordermanager.manager import OrderManager
from horizon.internal.portfolio.tracker import PortfolioTracker
from horizon.internal.proposals.queue import ProposalQueue


def _setup_logging() -> None:
    """Configure Python logging with structured format."""
    log_level = getattr(logging, settings.app.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def _load_exchange_credentials() -> dict[str, Any]:
    """Load exchange API credentials from key.txt file."""
    try:
        return load_keys_from_file("key.txt")
    except Exception as e:
        logging.warning("Failed to load keys from key.txt: %s", e)
        return {}


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
    db = await init_db(settings.database.path, migrations_dir)
    logger.info("Database initialized at %s", settings.database.path)

    # 2. Exchange registry setup
    registry = ExchangeRegistry()
    credentials = _load_exchange_credentials()

    # Register Binance if enabled
    binance_config = settings.exchanges.binance
    if binance_config.enabled:
        creds = credentials.get("binance", {})
        adapter = BinanceAdapter(
            api_key=creds.get("api_key", binance_config.api_key or ""),
            api_secret=creds.get("secret", binance_config.api_secret or ""),
            recv_window_ms=binance_config.recv_window_ms,
        )
        registry.register(adapter)
        logger.info("Binance adapter registered (enabled=%s)", adapter.enabled)

    # Register HTX if enabled
    htx_config = settings.exchanges.htx
    if htx_config.enabled:
        creds = credentials.get("htx", {})
        adapter = HTXAdapter(
            api_key=creds.get("api_key", htx_config.api_key or ""),
            api_secret=creds.get("secret", htx_config.api_secret or ""),
            recv_window_ms=htx_config.recv_window_ms,
        )
        registry.register(adapter)
        logger.info("HTX adapter registered (enabled=%s)", adapter.enabled)

    # Register Hyperliquid if enabled
    hyperliquid_config = settings.exchanges.hyperliquid
    if hyperliquid_config.enabled:
        creds = credentials.get("hyperliquid", {})
        adapter = HyperliquidAdapter(
            wallet_address=creds.get("wallet_address", hyperliquid_config.wallet_address or ""),
            private_key=creds.get("private_key", hyperliquid_config.private_key or ""),
        )
        registry.register(adapter)
        logger.info("Hyperliquid adapter registered (enabled=%s)", adapter.enabled)

    # Register Bitget if enabled
    bitget_config = settings.exchanges.bitget
    if bitget_config.enabled:
        creds = credentials.get("bitget", {})
        adapter = BitgetAdapter(
            api_key=creds.get("key", bitget_config.api_key or ""),
            api_secret=creds.get("secret", bitget_config.api_secret or ""),
            passphrase=creds.get("password", bitget_config.passphrase or ""),
        )
        registry.register(adapter)
        logger.info("Bitget adapter registered (enabled=%s)", adapter.enabled)

    # 3. Market data fetcher setup
    fetcher = MarketDataFetcher(
        registry=registry,
        symbols=settings.market_data.symbols,
        poll_interval_seconds=settings.trading.market_data_poll_interval_seconds,
    )

    # 4. Portfolio tracker setup
    portfolio_tracker = PortfolioTracker(
        registry=registry,
        db=db,
        fetcher=fetcher,
        snapshot_interval_seconds=settings.trading.portfolio_snapshot_interval_seconds,
    )

    # 5. Order manager setup
    order_manager = OrderManager(
        registry=registry,
        db=db,
    )

    # Store components in app state
    application.state.db = db
    application.state.registry = registry
    application.state.fetcher = fetcher
    application.state.order_manager = order_manager
    application.state.portfolio_tracker = portfolio_tracker

    # 6. Setup LLM engine and scheduler
    # Get LLM API key from settings or credentials
    llm_api_key = settings.llm.api_key
    if not llm_api_key:
        creds = _load_exchange_credentials()
        llm_api_key = creds.get("anthropic", {}).get("api_key", "")

    anthropic_client = anthropic.Anthropic(api_key=llm_api_key)

    # Load active strategy config from database
    cursor = await db.execute(
        "SELECT * FROM strategy_configs WHERE enabled = 1 LIMIT 1"
    )
    strategy_config_row = await cursor.fetchone()
    if strategy_config_row:
        strategy_config = dict(strategy_config_row)
    else:
        # Fallback to default config
        strategy_config = {
            "id": 1,
            "name": "default_long_term",
            "enabled": 1,
            "model": settings.llm.model,
            "analysis_interval_hours": settings.llm.analysis_interval_hours,
            "system_prompt": "You are a quantitative trading analyst for Horizon.",
            "asset_whitelist": '["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT"]',
            "max_position_pct": 20.0,
            "max_daily_loss_pct": 5.0,
            "min_confidence_threshold": 75,
            "max_risk_tier": "low",
        }

    # Create proposal queue
    proposal_queue = ProposalQueue(
        db=db,
        order_manager=order_manager,
        strategy_config=strategy_config,
    )

    # Create indicator calculator
    indicator_calculator = TechnicalIndicatorCalculator(db)

    # Create LLM strategy engine
    engine = LLMStrategyEngine(
        db=db,
        registry=registry,
        proposal_queue=proposal_queue,
        anthropic_client=anthropic_client,
        strategy_config=strategy_config,
        indicator_calculator=indicator_calculator,
        fetcher=fetcher,
    )

    # Create and start APScheduler
    scheduler = AsyncIOScheduler()
    interval_hours = strategy_config.get("analysis_interval_hours",8)
    scheduler.add_job(
        engine.analyze_and_propose,
        "interval",
        hours=interval_hours,
        id="llm_analysis",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "LLM Strategy Engine scheduled to run every %d hours",
        interval_hours,
    )

    # Store engine and scheduler in app state for access
    application.state.engine = engine
    application.state.scheduler = scheduler

    # 7. Start background tasks
    await fetcher.start()
    await portfolio_tracker.start()
    await order_manager.start_sync_loop()
    logger.info("All background tasks started")

    logger.info("Horizon server started on %s:%s", settings.app.host, settings.app.port)

    yield

    # ---- Shutdown ----
    logger.info("Stopping Horizon server...")

    # Stop scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("LLM scheduler stopped")

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
        db_path = settings.database.path
        parent_dir = os.path.dirname(db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        # Initialize database synchronously with migrations
        migrations_dir = os.path.join(os.path.dirname(__file__), "internal", "database", "migrations")
        db = asyncio.run(init_db(db_path, migrations_dir))

        # Create registry and register adapters
        registry = ExchangeRegistry()
        credentials = {}
        try:
            credentials = load_keys_from_file("key.txt")
        except Exception:
            pass

        # Register adapters
        binance_config = settings.exchanges.binance
        if binance_config.enabled:
            creds = credentials.get("binance", {})
            adapter = BinanceAdapter(
                api_key=creds.get("api_key", binance_config.api_key or ""),
                api_secret=creds.get("secret", binance_config.api_secret or ""),
                recv_window_ms=binance_config.recv_window_ms,
            )
            registry.register(adapter)

        htx_config = settings.exchanges.htx
        if htx_config.enabled:
            creds = credentials.get("htx", {})
            adapter = HTXAdapter(
                api_key=creds.get("api_key", htx_config.api_key or ""),
                api_secret=creds.get("secret", htx_config.api_secret or ""),
                recv_window_ms=htx_config.recv_window_ms,
            )
            registry.register(adapter)

        hyperliquid_config = settings.exchanges.hyperliquid
        if hyperliquid_config.enabled:
            creds = credentials.get("hyperliquid", {})
            adapter = HyperliquidAdapter(
                wallet_address=creds.get("wallet_address", hyperliquid_config.wallet_address or ""),
                private_key=creds.get("private_key", hyperliquid_config.private_key or ""),
            )
            registry.register(adapter)

        bitget_config = settings.exchanges.bitget
        if bitget_config.enabled:
            creds = credentials.get("bitget", {})
            adapter = BitgetAdapter(
                api_key=creds.get("key", bitget_config.api_key or ""),
                api_secret=creds.get("secret", bitget_config.api_secret or ""),
                passphrase=creds.get("password", bitget_config.passphrase or ""),
            )
            registry.register(adapter)

        # Create components
        fetcher = MarketDataFetcher(
            registry=registry,
            symbols=settings.market_data.symbols,
            poll_interval_seconds=settings.trading.market_data_poll_interval_seconds,
        )

        portfolio_tracker = PortfolioTracker(
            registry=registry,
            db=db,
            fetcher=fetcher,
            snapshot_interval_seconds=settings.trading.portfolio_snapshot_interval_seconds,
        )

        order_manager = OrderManager(
            registry=registry,
            db=db,
        )

        # Create proposal queue
        strategy_config = {
            "id": 1,
            "name": "default_long_term",
            "enabled": 1,
            "model": settings.llm.model,
            "analysis_interval_hours": settings.llm.analysis_interval_hours,
            "system_prompt": "You are a quantitative trading analyst for Horizon.",
            "asset_whitelist": '["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT"]',
            "max_position_pct": 20.0,
            "max_daily_loss_pct": 5.0,
            "min_confidence_threshold": 75,
            "max_risk_tier": "low",
        }
        proposal_queue = ProposalQueue(
            db=db,
            order_manager=order_manager,
            strategy_config=strategy_config,
        )

        # Call the web server's create_app - this returns an app with all endpoints
        application = _create_app_func(
            settings=settings,
            db=db,
            registry=registry,
            fetcher=fetcher,
            order_manager=order_manager,
            portfolio_tracker=portfolio_tracker,
            proposal_queue=proposal_queue,
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